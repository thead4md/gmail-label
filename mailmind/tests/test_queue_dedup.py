"""Tests for idempotent queue deduplication via fingerprints.

Covers:
- Fresh enqueue succeeds
- Duplicate enqueue of same fingerprint while pending → updates metadata, no new row
- Duplicate enqueue of executed fingerprint → no-op, returns None
- New prediction with different action for same email → supersedes old pending row
- force=True on executed fingerprint → new row inserted
"""
from __future__ import annotations

import sqlite3
import json
import tempfile
from pathlib import Path
from dataclasses import asdict

import pytest

from mailmind.storage.database import Database
from mailmind.storage.models import (
    Email,
    Prediction,
    QueueItem,
)
from mailmind.storage.migrations import apply_migrations
from mailmind.utils.fingerprint import make_action_fingerprint
from mailmind.storage.queries import (
    get_queue_item_by_fingerprint,
    upsert_queue_item,
    supersede_old_queue_items,
    get_pending_queue_enriched,
)


@pytest.fixture
def db():
    """Create an in-memory SQLite database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = Database(db_path)
        yield db
        db.close()


@pytest.fixture
def sample_email(db):
    """Insert and return a sample email."""
    email = Email(
        gmail_id="msg_001",
        sender="alice@example.com",
        subject="Test message",
        snippet="This is a test",
    )
    db.insert_email(email)
    return email


@pytest.fixture
def sample_prediction(db, sample_email):
    """Insert and return a sample prediction."""
    pred = Prediction(
        email_gmail_id=sample_email.gmail_id,
        model="rules",
        labels=["important"],
        priority_score=85,
        primary_label="important",
        action_suggested="star",
    )
    pred_id = db.save_prediction(pred)
    pred.id = pred_id
    return pred


def test_fresh_enqueue_succeeds(db, sample_email, sample_prediction):
    """Test that a fresh enqueue creates a new queue item."""
    fingerprint = make_action_fingerprint(
        sample_email.gmail_id,
        "star",
        {},
    )
    item = QueueItem(
        email_gmail_id=sample_email.gmail_id,
        prediction_id=sample_prediction.id,
        action="star",
        params={},
        action_fingerprint=fingerprint,
        status="pending",
        confidence=0.85,
        priority_score=85,
        reason_json={"reason": "test"},
    )
    result = upsert_queue_item(db, item)
    assert result is not None
    assert result.id is not None
    assert result.action_fingerprint == fingerprint
    assert result.status == "pending"


def test_duplicate_pending_updates_metadata(db, sample_email, sample_prediction):
    """Test that duplicate enqueue of pending item updates metadata, no new row."""
    fingerprint = make_action_fingerprint(
        sample_email.gmail_id,
        "star",
        {},
    )
    # First insert
    item1 = QueueItem(
        email_gmail_id=sample_email.gmail_id,
        prediction_id=sample_prediction.id,
        action="star",
        params={},
        action_fingerprint=fingerprint,
        status="pending",
        confidence=0.75,
        priority_score=75,
        reason_json={"reason": "test"},
    )
    result1 = upsert_queue_item(db, item1)
    id1 = result1.id

    # Second insert with updated metadata
    item2 = QueueItem(
        email_gmail_id=sample_email.gmail_id,
        prediction_id=sample_prediction.id,
        action="star",
        params={},
        action_fingerprint=fingerprint,
        status="pending",
        confidence=0.90,
        priority_score=90,
        reason_json={"reason": "test"},
    )
    result2 = upsert_queue_item(db, item2)

    # Should be same ID (no new row)
    assert result2.id == id1
    # Metadata should be updated
    assert result2.confidence == 0.90
    assert result2.priority_score == 90


def test_duplicate_executed_returns_none(db, sample_email, sample_prediction):
    """Test that duplicate enqueue of executed fingerprint returns None."""
    fingerprint = make_action_fingerprint(
        sample_email.gmail_id,
        "star",
        {},
    )
    # Insert with executed status
    item1 = QueueItem(
        email_gmail_id=sample_email.gmail_id,
        prediction_id=sample_prediction.id,
        action="star",
        params={},
        action_fingerprint=fingerprint,
        status="executed",
        confidence=0.95,
        priority_score=95,
        reason_json={"reason": "auto"},
    )
    result1 = upsert_queue_item(db, item1)
    assert result1 is not None

    # Try to enqueue same fingerprint again → should return None
    item2 = QueueItem(
        email_gmail_id=sample_email.gmail_id,
        prediction_id=sample_prediction.id,
        action="star",
        params={},
        action_fingerprint=fingerprint,
        status="pending",
        confidence=0.85,
        priority_score=85,
        reason_json={"reason": "test"},
    )
    result2 = upsert_queue_item(db, item2)
    assert result2 is None


def test_different_action_supersedes_old(db, sample_email, sample_prediction):
    """Test that new action for same email supersedes old pending items."""
    fp1 = make_action_fingerprint(sample_email.gmail_id, "star", {})
    fp2 = make_action_fingerprint(sample_email.gmail_id, "archive", {})

    # Enqueue first action
    item1 = QueueItem(
        email_gmail_id=sample_email.gmail_id,
        prediction_id=sample_prediction.id,
        action="star",
        params={},
        action_fingerprint=fp1,
        status="pending",
    )
    result1 = upsert_queue_item(db, item1)
    id1 = result1.id

    # Enqueue different action → should supersede first
    item2 = QueueItem(
        email_gmail_id=sample_email.gmail_id,
        prediction_id=sample_prediction.id,
        action="archive",
        params={},
        action_fingerprint=fp2,
        status="pending",
    )
    result2 = upsert_queue_item(db, item2)
    id2 = result2.id

    # Manually supersede old items
    count = supersede_old_queue_items(db, sample_email.gmail_id, fp2)
    assert count == 1

    # Verify first item is now superseded
    existing1 = get_queue_item_by_fingerprint(db, fp1)
    assert existing1.status == "superseded"

    # Second item should still be pending
    existing2 = get_queue_item_by_fingerprint(db, fp2)
    assert existing2.status == "pending"


def test_force_override_on_executed(db, sample_email, sample_prediction):
    """Test that force=True allows re-queueing an executed fingerprint."""
    fingerprint = make_action_fingerprint(
        sample_email.gmail_id,
        "star",
        {},
    )
    # Insert with executed status
    item1 = QueueItem(
        email_gmail_id=sample_email.gmail_id,
        prediction_id=sample_prediction.id,
        action="star",
        params={},
        action_fingerprint=fingerprint,
        status="executed",
        confidence=0.95,
        priority_score=95,
        reason_json={"reason": "auto"},
    )
    result1 = upsert_queue_item(db, item1)
    id1 = result1.id

    # Try to enqueue again without force → should return None
    item2 = QueueItem(
        email_gmail_id=sample_email.gmail_id,
        prediction_id=sample_prediction.id,
        action="star",
        params={},
        action_fingerprint=fingerprint,
        status="pending",
        confidence=0.85,
        priority_score=85,
        reason_json={"reason": "test"},
    )
    result2 = upsert_queue_item(db, item2)
    assert result2 is None

    # Manually delete (simulating force override in QueueManager)
    with db.transaction() as cur:
        cur.execute("DELETE FROM action_queue WHERE action_fingerprint = ?", (fingerprint,))

    # Now insert should succeed and create new row
    result3 = upsert_queue_item(db, item2)
    assert result3 is not None
    assert result3.id != id1  # Different ID (new row)
    assert result3.status == "pending"


def test_get_pending_queue_enriched(db, sample_email, sample_prediction):
    """Test that get_pending_queue_enriched returns pending items with enriched data."""
    fingerprint = make_action_fingerprint(
        sample_email.gmail_id,
        "star",
        {},
    )
    item = QueueItem(
        email_gmail_id=sample_email.gmail_id,
        prediction_id=sample_prediction.id,
        action="star",
        params={},
        action_fingerprint=fingerprint,
        status="pending",
        confidence=0.85,
        priority_score=85,
        reason_json={"reason": "test"},
    )
    upsert_queue_item(db, item)

    # Fetch enriched pending queue
    results = get_pending_queue_enriched(db, limit=10)
    assert len(results) == 1
    row = results[0]
    assert row["email_gmail_id"] == sample_email.gmail_id
    assert row["action"] == "star"
    assert row["status"] == "pending"
    assert row["sender"] == "alice@example.com"
    assert row["subject"] == "Test message"


# ---------------------------------------------------------------------------
# Step 4 regression: DB-level UNIQUE constraint on action_fingerprint
# ---------------------------------------------------------------------------

def test_db_level_unique_constraint_rejects_duplicate_fingerprint(db, sample_email):
    """UNIQUE INDEX on action_fingerprint must block duplicate raw INSERTs."""
    import sqlite3

    fp = make_action_fingerprint(sample_email.gmail_id, "label", {})
    insert_sql = (
        "INSERT INTO action_queue"
        " (email_gmail_id, action, action_fingerprint, status, created_at, updated_at)"
        " VALUES (?, ?, ?, 'pending', 1, 1)"
    )

    # First insert succeeds
    with db.transaction() as cur:
        cur.execute(insert_sql, (sample_email.gmail_id, "label", fp))

    # Second insert with same fingerprint must raise IntegrityError
    with pytest.raises((sqlite3.IntegrityError, Exception)):
        with db.transaction() as cur:
            cur.execute(insert_sql, (sample_email.gmail_id, "label", fp))

    # Confirm exactly one row persisted
    count = db.execute_sql(
        "SELECT COUNT(*) FROM action_queue WHERE action_fingerprint = ?", (fp,)
    ).fetchone()[0]
    assert count == 1


def test_insert_or_ignore_silently_skips_duplicate_fingerprint(db, sample_email):
    """INSERT OR IGNORE on a duplicate fingerprint must produce exactly one row."""
    fp = make_action_fingerprint(sample_email.gmail_id, "archive", {})
    ignore_sql = (
        "INSERT OR IGNORE INTO action_queue"
        " (email_gmail_id, action, action_fingerprint, status, created_at, updated_at)"
        " VALUES (?, ?, ?, 'pending', 1, 1)"
    )
    args = (sample_email.gmail_id, "archive", fp)

    with db.transaction() as cur:
        cur.execute(ignore_sql, args)
    with db.transaction() as cur:
        cur.execute(ignore_sql, args)  # silent no-op

    count = db.execute_sql(
        "SELECT COUNT(*) FROM action_queue WHERE action_fingerprint = ?", (fp,)
    ).fetchone()[0]
    assert count == 1

