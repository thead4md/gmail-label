"""Tests for P2A: dashboard Approve now actually executes the action.

Before: handle_approve() merely flipped status to 'approved'; nothing
applied the action to Gmail. The audit found this dead-end loop.

After: an optional executor parameter wires Approve straight to
ActionExecutor.execute_action. Without an executor the legacy behavior
is preserved (back-compat for existing tests/dashboards).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mailmind.intelligence.feedback import handle_approve
from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def _seed_queue_item(db: Database, *, status: str = "pending",
                     primary_label: str = "WORK", confidence: float = 0.95) -> int:
    """Seed an email + prediction + a queued action; return the queue id."""
    db.insert_email(Email(
        gmail_id="g1",
        sender="alice@example.com",
        subject="subj",
        snippet="x",
        body_text="b",
        recipients=["me@example.com"],
        date_ts=1,
        labels=[],
        parsed=True,
    ))
    db.save_prediction(Prediction(
        email_gmail_id="g1",
        model="rules",
        labels=[primary_label],
        priority_score=int(confidence * 100),
        primary_label=primary_label,
        confidence=confidence,
        pipeline_used="rules",
        rule_matches=[],
        scoring_breakdown="{}",
    ))
    cur = db.execute_sql(
        "INSERT INTO action_queue "
        "(email_gmail_id, action, status, confidence, priority_score) "
        "VALUES (?, ?, ?, ?, ?)",
        ("g1", "label", status, confidence, int(confidence * 100)),
    )
    db._conn.commit()
    return cur.lastrowid


def _status_of(db: Database, queue_id: int) -> str:
    row = db.execute_sql(
        "SELECT status FROM action_queue WHERE id = ?", (queue_id,)
    ).fetchone()
    return row["status"]


class TestExecutorIntegration:
    def test_executor_invoked_with_email_and_action(self, db: Database):
        qid = _seed_queue_item(db)
        executor = MagicMock()
        executor.execute_action.return_value = True

        ok = handle_approve(db, qid, executor=executor)

        assert ok is True
        executor.execute_action.assert_called_once()
        call_email, call_action, call_score = executor.execute_action.call_args.args
        assert call_email.gmail_id == "g1"
        assert call_action == "label"
        # ScoreResult.primary_label propagates from the prediction (essential for
        # the safety policy's auto-archive guard).
        assert call_score.primary_label == "WORK"

    def test_successful_execution_marks_executed(self, db: Database):
        qid = _seed_queue_item(db)
        executor = MagicMock()
        executor.execute_action.return_value = True

        handle_approve(db, qid, executor=executor)
        assert _status_of(db, qid) == "executed"

    def test_failed_execution_marks_execute_failed(self, db: Database):
        qid = _seed_queue_item(db)
        executor = MagicMock()
        executor.execute_action.return_value = False  # safety blocked it

        handle_approve(db, qid, executor=executor)
        assert _status_of(db, qid) == "execute_failed"

    def test_executor_exception_does_not_propagate(self, db: Database):
        qid = _seed_queue_item(db)
        executor = MagicMock()
        executor.execute_action.side_effect = RuntimeError("Gmail unavailable")

        # Must not raise — dashboard click should never crash the page.
        ok = handle_approve(db, qid, executor=executor)
        assert ok is True
        assert _status_of(db, qid) == "execute_failed"


class TestLegacyBehavior:
    def test_no_executor_keeps_approved_status(self, db: Database):
        """Existing call sites without an executor still get the old behavior."""
        qid = _seed_queue_item(db)
        ok = handle_approve(db, qid)  # executor omitted
        assert ok is True
        assert _status_of(db, qid) == "approved"

    def test_missing_queue_id_returns_false(self, db: Database):
        ok = handle_approve(db, 9999, executor=MagicMock())
        assert ok is False

    def test_missing_email_falls_back_to_approved(self, db: Database):
        """If the queue references a deleted email, don't blow up — keep audit."""
        # Insert a queue row that points at an email we never inserted.
        cur = db.execute_sql(
            "INSERT INTO action_queue "
            "(email_gmail_id, action, status, confidence, priority_score) "
            "VALUES (?, ?, ?, ?, ?)",
            ("ghost", "label", "pending", 0.9, 90),
        )
        db._conn.commit()
        qid = cur.lastrowid

        executor = MagicMock()
        ok = handle_approve(db, qid, executor=executor)

        assert ok is True
        # Executor was NEVER called — we don't blindly invent an Email.
        executor.execute_action.assert_not_called()
        assert _status_of(db, qid) == "approved"
