"""Tests for multi-account (second mailbox) support.

Covers the `account` dimension added to emails/predictions/action_queue and
the account-aware read queries. Sender data is intentionally shared across
accounts, so it is not account-scoped.
"""
from __future__ import annotations

import pytest

from mailmind.config import MailMindConfig
from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction
from mailmind.storage.queries import (
    get_recent_predictions_with_emails,
    get_pending_queue,
)


class TestAccountConfig:
    def test_accounts_from_mailmind_accounts(self, monkeypatch):
        monkeypatch.setenv("MAILMIND_ACCOUNTS", "a@x.com, b@y.com ")
        accounts = MailMindConfig.load_accounts()
        assert accounts == ["a@x.com", "b@y.com"]

    def test_falls_back_to_user_email(self, monkeypatch):
        monkeypatch.delenv("MAILMIND_ACCOUNTS", raising=False)
        monkeypatch.setenv("MAILMIND_USER_EMAIL", "solo@x.com")
        assert MailMindConfig.load_accounts() == ["solo@x.com"]

    def test_primary_account(self, monkeypatch):
        monkeypatch.setenv("MAILMIND_ACCOUNTS", "first@x.com,second@y.com")
        cfg = MailMindConfig.from_env()
        assert cfg.primary_account == "first@x.com"

    def test_empty_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("MAILMIND_ACCOUNTS", raising=False)
        monkeypatch.delenv("MAILMIND_USER_EMAIL", raising=False)
        assert MailMindConfig.load_accounts() == []


class TestPerAccountTokenStorage:
    def test_primary_uses_legacy_token_names(self):
        from mailmind.ingestion import auth
        assert auth._token_key_name(None) == "mailmind_gmail_token"
        assert auth._token_file(None).name == "tokens.json.enc"
        assert auth._token_env_var(None) == "GMAIL_TOKEN"

    def test_named_account_gets_suffixed_names(self):
        from mailmind.ingestion import auth
        acct = "dudas.adam@mcssz.hu"
        assert auth._token_key_name(acct) != auth._token_key_name(None)
        assert "mcssz" in auth._token_file(acct).name
        # Env var is a clean Fly-secret path for the second mailbox.
        assert auth._token_env_var(acct) == "GMAIL_TOKEN_DUDAS_ADAM_MCSSZ_HU"

    def test_env_var_token_fallback(self, monkeypatch):
        from mailmind.ingestion import auth
        monkeypatch.delenv("MAILMIND_DATA_DIR", raising=False)
        # Force keyring + encrypted-file lookups to miss, otherwise a real local
        # token (e.g. you just authenticated this account on your dev machine)
        # would short-circuit the env-var fallback we're trying to verify.
        monkeypatch.setattr(auth, "_load_token_from_keyring", lambda account=None: None)
        monkeypatch.setattr(auth, "_load_token_local_encrypted", lambda account=None: None)
        monkeypatch.setenv("GMAIL_TOKEN_DUDAS_ADAM_MCSSZ_HU", '{"token": "x"}')
        assert auth._load_stored_token("dudas.adam@mcssz.hu") == '{"token": "x"}'


class TestMultiAccountDispatch:
    def test_skips_unconnected_secondary_account(self, monkeypatch):
        """Secondary mailbox with no token is skipped, primary still runs."""
        import mailmind.main as main_mod

        monkeypatch.setenv("MAILMIND_ACCOUNTS", "primary@x.com,second@y.com")
        calls = []

        def fake_run_once(db, dry_run, fetch_max, no_llm=False, account=None,
                          auth_account=None, allow_interactive=True):
            calls.append({"account": account, "auth_account": auth_account,
                          "allow_interactive": allow_interactive})

        monkeypatch.setattr(main_mod, "_run_once", fake_run_once)
        main_mod._run_all_accounts(db=object(), dry_run=True, fetch_max=10)

        assert [c["account"] for c in calls] == ["primary@x.com", "second@y.com"]
        # Primary reuses legacy token + may auth interactively; secondary does not.
        assert calls[0]["auth_account"] is None and calls[0]["allow_interactive"] is True
        assert calls[1]["auth_account"] == "second@y.com" and calls[1]["allow_interactive"] is False

    def test_one_account_failure_does_not_abort_others(self, monkeypatch):
        import mailmind.main as main_mod

        monkeypatch.setenv("MAILMIND_ACCOUNTS", "a@x.com,b@y.com")
        seen = []

        def fake_run_once(db, dry_run, fetch_max, no_llm=False, account=None, **kw):
            seen.append(account)
            if account == "a@x.com":
                raise RuntimeError("boom")

        monkeypatch.setattr(main_mod, "_run_once", fake_run_once)
        main_mod._run_all_accounts(db=object(), dry_run=True, fetch_max=10)
        assert seen == ["a@x.com", "b@y.com"]  # b still ran despite a failing


class TestAuthCli:
    def test_auth_account_mapping(self, monkeypatch):
        """Primary maps to legacy token storage (None); secondary to its email."""
        import mailmind.main as main_mod

        monkeypatch.setenv("MAILMIND_ACCOUNTS", "primary@x.com,second@y.com")
        assert main_mod._auth_account_for(None) is None
        assert main_mod._auth_account_for("primary@x.com") is None
        assert main_mod._auth_account_for("second@y.com") == "second@y.com"

    def test_auth_command_connects_secondary(self, monkeypatch):
        """`auth --account second@y.com` authenticates with that account's token."""
        import mailmind.main as main_mod
        from click.testing import CliRunner

        monkeypatch.setenv("MAILMIND_ACCOUNTS", "primary@x.com,second@y.com")
        captured = {}

        def fake_authenticate(scopes=None, account=None):
            captured["account"] = account

            class _Creds:
                scopes = ["s"]

            return _Creds()

        monkeypatch.setattr(main_mod, "authenticate", fake_authenticate)
        result = CliRunner().invoke(main_mod.cli, ["auth", "--account", "second@y.com"])
        assert result.exit_code == 0, result.output
        assert captured["account"] == "second@y.com"

    def test_accounts_command_reports_status(self, monkeypatch):
        import mailmind.main as main_mod
        from click.testing import CliRunner

        monkeypatch.setenv("MAILMIND_ACCOUNTS", "primary@x.com,second@y.com")
        monkeypatch.setattr(
            "mailmind.ingestion.auth._load_stored_token", lambda account=None: None
        )
        result = CliRunner().invoke(main_mod.cli, ["accounts"])
        assert result.exit_code == 0, result.output
        assert "primary@x.com" in result.output
        assert "second@y.com" in result.output
        assert "NOT connected" in result.output


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def _email(gmail_id: str, account: str) -> Email:
    return Email(
        gmail_id=gmail_id,
        sender="s@example.com",
        subject="subj",
        snippet="x",
        body_text="body",
        recipients=["me@example.com"],
        date_ts=1,
        labels=[],
        parsed=True,
        account=account,
    )


def _pred(gmail_id: str, account: str, label: str = "WORK") -> Prediction:
    return Prediction(
        email_gmail_id=gmail_id,
        account=account,
        model="rules",
        labels=[label],
        priority_score=50,
        primary_label=label,
        confidence=0.9,
        pipeline_used="rules",
        rule_matches=[],
        scoring_breakdown="{}",
    )


class TestAccountWrites:
    def test_insert_email_persists_account(self, db: Database):
        db.insert_email(_email("g1", "a@x.com"))
        assert db.get_email_by_gmail_id("g1")["account"] == "a@x.com"

    def test_save_prediction_persists_account(self, db: Database):
        db.insert_email(_email("g1", "a@x.com"))
        db.save_prediction(_pred("g1", "a@x.com"))
        assert db.get_predictions_for_email("g1")[0]["account"] == "a@x.com"

    def test_two_accounts_coexist(self, db: Database):
        db.insert_email(_email("g1", "a@x.com"))
        db.insert_email(_email("g2", "b@y.com"))
        db.save_prediction(_pred("g1", "a@x.com"))
        db.save_prediction(_pred("g2", "b@y.com"))

        a_count = db.execute_sql(
            "SELECT COUNT(*) c FROM predictions WHERE account = 'a@x.com'"
        ).fetchone()["c"]
        b_count = db.execute_sql(
            "SELECT COUNT(*) c FROM predictions WHERE account = 'b@y.com'"
        ).fetchone()["c"]
        assert a_count == 1
        assert b_count == 1


class TestAccountFilteredReads:
    def _seed_two_accounts(self, db: Database):
        db.insert_email(_email("g1", "a@x.com"))
        db.insert_email(_email("g2", "b@y.com"))
        db.save_prediction(_pred("g1", "a@x.com", "WORK"))
        db.save_prediction(_pred("g2", "b@y.com", "NEWSLETTER"))

    def test_recent_predictions_filtered_by_account(self, db: Database):
        self._seed_two_accounts(db)

        a_rows = get_recent_predictions_with_emails(db, account="a@x.com")
        b_rows = get_recent_predictions_with_emails(db, account="b@y.com")
        all_rows = get_recent_predictions_with_emails(db)  # no filter

        assert {r["email_gmail_id"] for r in a_rows} == {"g1"}
        assert {r["email_gmail_id"] for r in b_rows} == {"g2"}
        assert {r["email_gmail_id"] for r in all_rows} == {"g1", "g2"}

    def test_pending_queue_filtered_by_account(self, db: Database):
        db.execute_sql(
            "INSERT INTO action_queue (email_gmail_id, action, status, account) VALUES (?, ?, 'pending', ?)",
            ("g1", "label", "a@x.com"),
        )
        db.execute_sql(
            "INSERT INTO action_queue (email_gmail_id, action, status, account) VALUES (?, ?, 'pending', ?)",
            ("g2", "label", "b@y.com"),
        )
        db._conn.commit()

        assert len(get_pending_queue(db, account="a@x.com")) == 1
        assert len(get_pending_queue(db, account="b@y.com")) == 1
        assert len(get_pending_queue(db)) == 2  # no filter = all
