"""Tests for P2D-2: activity digest.

build_digest summarizes MailMind's activity in a time window so the
dashboard and CLI can show "what did the watcher actually do today?"
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import mailmind.main as main_mod
from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction
from mailmind.storage.queries import build_digest


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def _email(gid: str, account: str = "a@x.com") -> Email:
    return Email(
        gmail_id=gid,
        sender="alice@example.com",
        subject="s",
        snippet="x",
        body_text="b",
        recipients=["me@example.com"],
        date_ts=1,
        labels=[],
        parsed=True,
        account=account,
    )


def _pred(gid: str, label: str, account: str = "a@x.com",
          created_at: int | None = None) -> Prediction:
    p = Prediction(
        email_gmail_id=gid,
        account=account,
        model="rules",
        labels=[label],
        priority_score=80,
        primary_label=label,
        confidence=0.9,
        pipeline_used="rules",
        rule_matches=[],
        scoring_breakdown="{}",
    )
    if created_at is not None:
        p.created_at = created_at
    return p


def _queue(db: Database, gid: str, status: str, *,
           account: str = "a@x.com", reason: str = "{}",
           created_at: int | None = None, updated_at: int | None = None,
           executed_at: int | None = None):
    db.execute_sql(
        "INSERT INTO action_queue "
        "(email_gmail_id, action, status, confidence, priority_score, "
        " reason_json, account, created_at, updated_at, executed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (gid, "label", status, 0.9, 80, reason, account,
         created_at or int(time.time()),
         updated_at or int(time.time()),
         executed_at),
    )
    db._conn.commit()


def _correction(db: Database, gid: str, ts: int):
    db.execute_sql(
        "INSERT INTO user_corrections "
        "(email_gmail_id, original_label, corrected_label, source, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (gid, "WORK", "NEWSLETTER", "dashboard", ts),
    )
    db._conn.commit()


class TestDigestCounters:
    def test_empty_db_yields_zeros(self, db: Database):
        d = build_digest(db, since_ts=0)
        assert d["classified"] == 0
        assert d["executed"] == 0
        assert d["execute_failed"] == 0
        assert d["queued"] == 0
        assert d["pending_reply_needed"] == 0
        assert d["corrections"] == 0
        assert d["top_labels"] == []

    def test_counts_classifications_in_window(self, db: Database):
        now = int(time.time())
        # In window (last hour)
        db.insert_email(_email("g1"))
        db.save_prediction(_pred("g1", "WORK", created_at=now - 100))
        # Out of window (2 days ago)
        db.insert_email(_email("g2"))
        db.save_prediction(_pred("g2", "WORK", created_at=now - 2 * 86400))

        d = build_digest(db, since_ts=now - 3600)
        assert d["classified"] == 1

    def test_counts_executed_and_failed(self, db: Database):
        now = int(time.time())
        db.insert_email(_email("g1"))
        _queue(db, "g1", "executed", executed_at=now - 100)
        db.insert_email(_email("g2"))
        _queue(db, "g2", "execute_failed", updated_at=now - 100)
        db.insert_email(_email("g3"))
        _queue(db, "g3", "executed", executed_at=now - 2 * 86400)  # too old

        d = build_digest(db, since_ts=now - 3600)
        assert d["executed"] == 1
        assert d["execute_failed"] == 1

    def test_queued_is_a_snapshot_not_windowed(self, db: Database):
        """Pending count is 'right now', not bounded by since_ts."""
        now = int(time.time())
        db.insert_email(_email("g1"))
        # Old pending item must still count — pending means it's STILL waiting.
        _queue(db, "g1", "pending", created_at=now - 30 * 86400,
               updated_at=now - 30 * 86400)

        d = build_digest(db, since_ts=now - 3600)
        assert d["queued"] == 1

    def test_pending_reply_needed_counted(self, db: Database):
        now = int(time.time())
        db.insert_email(_email("g1"))
        _queue(db, "g1", "pending", reason='{"reply_needed": true}')
        db.insert_email(_email("g2"))
        _queue(db, "g2", "pending", reason='{}')

        d = build_digest(db, since_ts=now - 3600)
        assert d["queued"] == 2
        assert d["pending_reply_needed"] == 1

    def test_corrections_counted_in_window(self, db: Database):
        now = int(time.time())
        _correction(db, "g1", ts=now - 100)
        _correction(db, "g2", ts=now - 2 * 86400)  # too old

        d = build_digest(db, since_ts=now - 3600)
        assert d["corrections"] == 1

    def test_top_labels_returned_sorted(self, db: Database):
        now = int(time.time())
        for i, lbl in enumerate(["WORK"] * 3 + ["NEWSLETTER"] * 2 + ["CALENDAR"]):
            gid = f"g{i}"
            db.insert_email(_email(gid))
            db.save_prediction(_pred(gid, lbl, created_at=now - 100))

        d = build_digest(db, since_ts=now - 3600)
        assert d["top_labels"][0] == {"label": "WORK", "count": 3}
        assert d["top_labels"][1] == {"label": "NEWSLETTER", "count": 2}
        # All three labels included (limit 5; we have 3).
        assert {r["label"] for r in d["top_labels"]} == {"WORK", "NEWSLETTER", "CALENDAR"}


class TestDigestAccountFilter:
    def test_account_scopes_counts(self, db: Database):
        now = int(time.time())
        db.insert_email(_email("g1", account="a@x.com"))
        db.save_prediction(_pred("g1", "WORK", account="a@x.com",
                                  created_at=now - 100))
        db.insert_email(_email("g2", account="b@y.com"))
        db.save_prediction(_pred("g2", "WORK", account="b@y.com",
                                  created_at=now - 100))

        d_a = build_digest(db, since_ts=now - 3600, account="a@x.com")
        d_all = build_digest(db, since_ts=now - 3600)
        assert d_a["classified"] == 1
        assert d_all["classified"] == 2


class TestDigestCli:
    def test_cli_prints_summary(self, db: Database, monkeypatch):
        now = int(time.time())
        db.insert_email(_email("g1"))
        db.save_prediction(_pred("g1", "WORK", created_at=now - 100))

        with patch.object(main_mod, "_get_db", return_value=db):
            result = CliRunner().invoke(main_mod.cli, ["digest", "--days", "1"])
        assert result.exit_code == 0, result.output
        assert "Classified:" in result.output
        assert "WORK" in result.output
