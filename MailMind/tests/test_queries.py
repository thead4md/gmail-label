"""Tests for mailmind/storage/queries.py.

Uses an in‑memory SQLite database with migrations applied.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from mailmind.storage.database import Database
from mailmind.storage.migrations import apply_migrations
from mailmind.storage.queries import (
    get_recent_predictions,
    get_predictions_for_email,
    get_recent_actions,
    get_sender_reputations,
    get_summary_metrics,
)


# ---------------------------------------------------------------------------
# Fixture: in‑memory Database with migrations applied
# ---------------------------------------------------------------------------

@pytest.fixture
def db() -> Database:
    """Create an in‑memory Database, apply migrations, and return it."""
    # Use a temporary file because the Database class expects a path
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    db = Database(db_path)
    # Apply migrations (they will create the tables)
    apply_migrations(db._conn)
    yield db
    db.close()
    db_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Helper to insert sample data
# ---------------------------------------------------------------------------

def _insert_email(db: Database, gmail_id: str = "test001", sender: str = "alice@example.com") -> None:
    db.execute_sql(
        """INSERT INTO emails (gmail_id, sender, subject, snippet, body_text, recipients, date_ts, labels)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (gmail_id, sender, "Test Subject", "Test snippet", "body", "[]", 1000000, "[]"),
    )


def _insert_prediction(
    db: Database,
    email_gmail_id: str = "test001",
    model: str = "rules_only",
    primary_label: str = "INBOX",
    score: int = 50,
    priority_score: int = 50,
    confidence: float = 0.8,
    rule_matches: Optional[str] = None,
    ml_confidence: Optional[float] = None,
    llm_confidence: Optional[float] = None,
    created_at: int = 1000000,
) -> None:
    db.execute_sql(
        """INSERT INTO predictions
           (email_gmail_id, model, primary_label, score, priority_score, confidence,
            rule_matches, ml_confidence, llm_confidence, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (email_gmail_id, model, primary_label, score, priority_score, confidence,
         rule_matches, ml_confidence, llm_confidence, created_at),
    )


def _insert_action(
    db: Database,
    email_gmail_id: str = "test001",
    action: str = "archive",
    succeeded: int = 1,
    dry_run: int = 0,
    created_at: int = 1000000,
) -> None:
    db.execute_sql(
        """INSERT INTO actions_applied (email_gmail_id, action, succeeded, dry_run, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (email_gmail_id, action, succeeded, dry_run, created_at),
    )


def _insert_sender_reputation(
    db: Database,
    sender: str = "alice@example.com",
    score: float = 0.5,
    last_seen: int = 1000000,
) -> None:
    db.execute_sql(
        """INSERT INTO sender_reputation (sender, score, last_seen)
           VALUES (?, ?, ?)""",
        (sender, score, last_seen),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetRecentPredictions:
    def test_empty_db(self, db: Database) -> None:
        result = get_recent_predictions(db)
        assert result == []

    def test_single_prediction(self, db: Database) -> None:
        _insert_email(db)
        _insert_prediction(db)
        result = get_recent_predictions(db)
        assert len(result) == 1
        assert result[0]["email_gmail_id"] == "test001"
        assert result[0]["model"] == "rules_only"
        assert result[0]["primary_label"] == "INBOX"
        assert result[0]["score"] == 50
        assert result[0]["confidence"] == 0.8

    def test_multiple_predictions_ordered(self, db: Database) -> None:
        _insert_email(db, gmail_id="a")
        _insert_email(db, gmail_id="b")
        _insert_prediction(db, email_gmail_id="a", created_at=200)
        _insert_prediction(db, email_gmail_id="b", created_at=100)
        result = get_recent_predictions(db)
        assert len(result) == 2
        # Most recent first
        assert result[0]["email_gmail_id"] == "a"
        assert result[1]["email_gmail_id"] == "b"

    def test_limit(self, db: Database) -> None:
        for i in range(5):
            gid = f"test{i}"
            _insert_email(db, gmail_id=gid)
            _insert_prediction(db, email_gmail_id=gid, created_at=i)
        result = get_recent_predictions(db, limit=3)
        assert len(result) == 3


class TestGetPredictionsForEmail:
    def test_no_predictions(self, db: Database) -> None:
        result = get_predictions_for_email(db, "nonexistent")
        assert result == []

    def test_multiple_for_same_email(self, db: Database) -> None:
        _insert_email(db)
        _insert_prediction(db, created_at=100)
        _insert_prediction(db, created_at=200)
        result = get_predictions_for_email(db, "test001")
        assert len(result) == 2
        # Most recent first
        assert result[0]["created_at"] == 200
        assert result[1]["created_at"] == 100


class TestGetRecentActions:
    def test_empty(self, db: Database) -> None:
        assert get_recent_actions(db) == []

    def test_single_action(self, db: Database) -> None:
        _insert_email(db)
        _insert_action(db)
        result = get_recent_actions(db)
        assert len(result) == 1
        assert result[0]["action"] == "archive"
        assert result[0]["succeeded"] == 1

    def test_limit(self, db: Database) -> None:
        for i in range(5):
            _insert_email(db, gmail_id=f"test{i}")
            _insert_action(db, email_gmail_id=f"test{i}", created_at=i)
        result = get_recent_actions(db, limit=2)
        assert len(result) == 2


class TestGetSenderReputations:
    def test_empty(self, db: Database) -> None:
        assert get_sender_reputations(db) == []

    def test_single(self, db: Database) -> None:
        _insert_sender_reputation(db)
        result = get_sender_reputations(db)
        assert len(result) == 1
        assert result[0]["sender"] == "alice@example.com"
        assert result[0]["score"] == 0.5

    def test_ordered_by_score_desc(self, db: Database) -> None:
        _insert_sender_reputation(db, sender="low@example.com", score=0.1)
        _insert_sender_reputation(db, sender="high@example.com", score=0.9)
        result = get_sender_reputations(db)
        assert result[0]["sender"] == "high@example.com"
        assert result[1]["sender"] == "low@example.com"


class TestGetSummaryMetrics:
    def test_empty(self, db: Database) -> None:
        metrics = get_summary_metrics(db)
        assert metrics == {"emails": 0, "predictions": 0, "actions": 0}

    def test_counts(self, db: Database) -> None:
        _insert_email(db)
        _insert_email(db, gmail_id="test002")
        _insert_prediction(db)
        _insert_action(db)
        metrics = get_summary_metrics(db)
        assert metrics["emails"] == 2
        assert metrics["predictions"] == 1
        assert metrics["actions"] == 1
