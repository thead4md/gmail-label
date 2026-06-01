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
