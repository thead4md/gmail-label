"""Tests for the local-cache retention sweep (Database.prune_old_data).

prune_old_data deletes old emails and their associated rows from the LOCAL
SQLite cache only. It must:
  - delete emails older than the retention window (+ their children)
  - keep recent emails
  - never drop an email that still has a PENDING action-queue item
  - never drop emails with a NULL date_ts (un-ageable)
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import time

import pytest

from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction


@pytest.fixture
def db():
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test_retention.db"
    database = Database(str(db_path))
    yield database
    database.close()
    for p in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if p.exists():
            p.unlink()
    tmp_dir.rmdir()


def _email(gmail_id: str, date_ts) -> Email:
    return Email(
        gmail_id=gmail_id,
        sender="a@example.com",
        subject="s",
        snippet="x",
        body_text="body",
        recipients=["me@example.com"],
        date_ts=date_ts,
        labels=[],
        parsed=True,
    )


def _pred(gmail_id: str) -> Prediction:
    return Prediction(
        email_gmail_id=gmail_id,
        model="rules",
        labels=["NEWSLETTER"],
        priority_score=10,
        primary_label="NEWSLETTER",
        confidence=0.85,
        pipeline_used="rules",
        rule_matches=[],
        scoring_breakdown="{}",
    )


def test_prunes_old_keeps_recent(db: Database):
    now = int(time.time())
    old_ts = now - 200 * 86400      # 200 days old
    recent_ts = now - 10 * 86400    # 10 days old

    db.insert_email(_email("old", old_ts))
    db.save_prediction(_pred("old"))
    db.insert_email(_email("recent", recent_ts))
    db.save_prediction(_pred("recent"))

    counts = db.prune_old_data(retention_days=90)

    assert counts["emails"] == 1
    assert counts["predictions"] == 1
    assert db.get_email_by_gmail_id("old") is None
    assert db.get_email_by_gmail_id("recent") is not None
    assert db.has_prediction("recent") is True
    assert db.has_prediction("old") is False


def test_pending_review_item_is_protected(db: Database):
    now = int(time.time())
    old_ts = now - 200 * 86400

    db.insert_email(_email("old_pending", old_ts))
    db.save_prediction(_pred("old_pending"))
    # An old email still awaiting review must survive the sweep.
    db.execute_sql(
        "INSERT INTO action_queue (email_gmail_id, action, status) VALUES (?, ?, 'pending')",
        ("old_pending", "label"),
    )
    db._conn.commit()

    db.prune_old_data(retention_days=90)

    assert db.get_email_by_gmail_id("old_pending") is not None
    assert db.has_prediction("old_pending") is True


def test_null_date_email_is_preserved(db: Database):
    db.insert_email(_email("no_date", None))
    db.save_prediction(_pred("no_date"))

    db.prune_old_data(retention_days=90)

    assert db.get_email_by_gmail_id("no_date") is not None


def test_resolved_queue_item_does_not_protect(db: Database):
    now = int(time.time())
    old_ts = now - 200 * 86400

    db.insert_email(_email("old_done", old_ts))
    db.save_prediction(_pred("old_done"))
    db.execute_sql(
        "INSERT INTO action_queue (email_gmail_id, action, status) VALUES (?, ?, 'approved')",
        ("old_done", "label"),
    )
    db._conn.commit()

    counts = db.prune_old_data(retention_days=90)

    assert db.get_email_by_gmail_id("old_done") is None
    assert counts["action_queue"] == 1


def test_vacuum_runs(db: Database):
    db.insert_email(_email("x", int(time.time())))
    # Should not raise (VACUUM must run outside a transaction).
    db.vacuum()


def test_state_roundtrip(db: Database):
    assert db.get_state("last_prune_ts") is None
    db.set_state("last_prune_ts", "12345")
    assert db.get_state("last_prune_ts") == "12345"
    db.set_state("last_prune_ts", "67890")
    assert db.get_state("last_prune_ts") == "67890"
