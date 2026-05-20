"""End-to-end test for Pass 6: Human-in-the-loop review loop.

Tests the full pipeline without Gmail API:
1. Insert a mock Email into DB.
2. Run pipeline.process() -> get Prediction with action_suggested set.
3. Run queue_manager.enqueue_from_prediction() -> verify status='queued'.
4. Call db.update_action_queue_status(id, 'approved') -> verify status updated.
5. Verify action_queue row has status='approved'.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from mailmind.actions.executor import ActionExecutor
from mailmind.actions.safety import SafetyPolicy
from mailmind.processing.pipeline import Pipeline
from mailmind.processing.queue_manager import QueueManager
from mailmind.processing.rules import RulesEngine
from mailmind.processing.scorer import PriorityScorer, ScoreResult
from mailmind.storage.database import Database
from mailmind.storage.models import Email


@pytest.fixture
def memory_db():
    """Fixture for an in-memory SQLite database."""
    db = Database(":memory:")
    yield db
    db.close()


@pytest.fixture
def mock_gmail_service():
    """Fixture for a mocked Gmail API service."""
    return MagicMock()


def test_e2e_human_review_loop(memory_db: Database, mock_gmail_service: MagicMock):
    """Test the full flow from processing to queuing and manual approval."""
    # 1. Setup components
    db = memory_db
    safety = SafetyPolicy(dry_run=True)
    executor = ActionExecutor(mock_gmail_service, db, safety)
    rules = RulesEngine()
    scorer = PriorityScorer()
    pipeline = Pipeline(db, rules, scorer, executor, safety)
    queue_manager = QueueManager(executor)

    # 2. Insert a mock email into the database
    mock_email = Email(
        gmail_id="test_e2e_123",
        sender="important@example.com",
        recipients=["user@example.com"],
        subject="Action Required",
        body_text="This email should be queued for review.",
        date_ts=1672531200,
    )
    db.insert_email(mock_email)

    # 3. Create a mock ScoreResult that yields confidence=0.80 (queues, not auto-executes)
    # total_score=80 gives confidence=0.80 which is in [0.65, 0.90) -> queued
    score_result_to_return = ScoreResult(
        total_score=80,
        base_score=50,
        rule_contribution=30,
        direct_mention_bonus=0,
        recency_bonus=0,
        sender_trust=0,
        primary_label="IMPORTANT",
    )

    # 4. Run the processing pipeline with the mocked score
    with patch(
        "mailmind.processing.scorer.PriorityScorer.compute_score",
        return_value=score_result_to_return,
    ):
        prediction = pipeline.process(email=mock_email, auto_action=False)

    # 5. Retrieve prediction from DB to get its ID
    preds_from_db = db.get_predictions_for_email(mock_email.gmail_id)
    assert len(preds_from_db) == 1, "Should have exactly one prediction in DB"
    prediction.id = preds_from_db[0]["id"]

    # Ensure scoring_breakdown is set for the queue manager
    prediction.scoring_breakdown = json.dumps(asdict(score_result_to_return))

    # 6. Run the queue manager and verify the action is queued
    status = queue_manager.enqueue_from_prediction(
        db, mock_email, score_result_to_return, prediction
    )
    assert status == "queued", (
        f"Expected 'queued' status, got '{status}'. "
        f"Confidence would be {score_result_to_return.total_score / 100.0}"
    )

    # 7. Verify the item exists in the action_queue with 'pending' status
    queue_items = db.execute_sql("SELECT * FROM action_queue").fetchall()
    assert len(queue_items) == 1, "Should have exactly one action_queue row"
    queue_item = queue_items[0]
    assert queue_item["status"] == "pending"
    queue_id = queue_item["id"]

    # 8. Simulate human approval by updating the queue item's status
    db.update_action_queue_status(queue_id, "approved")

    # 9. Verify the status was updated successfully
    updated_item = db.execute_sql(
        "SELECT * FROM action_queue WHERE id = ?", (queue_id,)
    ).fetchone()
    assert updated_item is not None, "Queue item should still exist after status update"
    assert updated_item["status"] == "approved", (
        f"Expected status='approved', got '{updated_item['status']}'"
    )

    # 10. Verify invalid status raises ValueError
    with pytest.raises(ValueError):
        db.update_action_queue_status(queue_id, "invalid_status")
