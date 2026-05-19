"""Manages the action queue for human-in-the-loop review.

Decides whether to execute, queue, or skip actions based on
prediction confidence scores.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mailmind.storage.database import Database
    from mailmind.storage.models import Email, Prediction
    from mailmind.processing.scorer import ScoreResult
    from mailmind.actions.executor import ActionExecutor

LOG = logging.getLogger(__name__)


class QueueManager:
    """Enqueues actions based on prediction confidence.

    Confidence tiers:
        >= 0.90: Auto-execute immediately (auto_eligible=1, status='executed')
        0.65 <= x < 0.90: Queue for human review (status='pending')
        < 0.65: Skip, do nothing
    """

    AUTO_EXECUTE_THRESHOLD = 0.90
    QUEUE_THRESHOLD = 0.65

    def __init__(self, executor: "ActionExecutor"):
        """Initialize QueueManager.

        Args:
            executor: An ActionExecutor instance to execute high-confidence actions.
        """
        self.executor = executor

    def enqueue_from_prediction(
        self,
        db: "Database",
        email: "Email",
        score_result: "ScoreResult",
        prediction: "Prediction",
    ) -> str:
        """Enqueue or execute an action based on a prediction's confidence score.

        Args:
            db: Database instance for persistence.
            email: The email being processed.
            score_result: ScoreResult containing the total score and breakdown.
            prediction: Prediction model from the pipeline (must have ``id`` set).

        Returns:
            Status string: 'executed', 'queued', or 'skipped'.
        """
        confidence = score_result.total_score / 100.0
        suggested_action = prediction.action_suggested

        # If no action is suggested, skip
        if not suggested_action:
            LOG.debug(
                "No suggested action for %s; skipping queue.",
                email.gmail_id,
            )
            return "skipped"

        # Ensure we have a prediction ID for the foreign key
        prediction_id = getattr(prediction, "id", None)
        if prediction_id is None:
            LOG.error(
                "Prediction has no id for email %s; cannot enqueue. "
                "Falling back to DB lookup.",
                email.gmail_id,
            )
            rows = db.get_predictions_for_email(email.gmail_id)
            if rows:
                prediction_id = rows[0]["id"]
            else:
                LOG.error(
                    "No prediction found in DB for email %s; skipping queue.",
                    email.gmail_id,
                )
                return "skipped"

        # Tier 1: High confidence - auto-execute
        if confidence >= self.AUTO_EXECUTE_THRESHOLD:
            LOG.info(
                "Auto-executing '%s' for %s (confidence=%.2f >= %.2f)",
                suggested_action,
                email.gmail_id,
                confidence,
                self.AUTO_EXECUTE_THRESHOLD,
            )
            # Execute the action via executor
            self.executor.execute_action(email, suggested_action, score_result)
            # Log to action_queue with auto_eligible=1 and status='executed'
            with db.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO action_queue
                        (email_gmail_id, prediction_id, suggested_action,
                         primary_label, confidence, auto_eligible, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        email.gmail_id,
                        prediction_id,
                        suggested_action,
                        prediction.primary_label,
                        confidence,
                        1,  # auto_eligible
                        "executed",
                    ),
                )
            return "executed"

        # Tier 2: Medium confidence - queue for human review
        if confidence >= self.QUEUE_THRESHOLD:
            LOG.info(
                "Queueing '%s' for %s (confidence=%.2f in [%.2f, %.2f))",
                suggested_action,
                email.gmail_id,
                confidence,
                self.QUEUE_THRESHOLD,
                self.AUTO_EXECUTE_THRESHOLD,
            )
            with db.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO action_queue
                        (email_gmail_id, prediction_id, suggested_action,
                         primary_label, confidence, auto_eligible, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        email.gmail_id,
                        prediction_id,
                        suggested_action,
                        prediction.primary_label,
                        confidence,
                        0,  # not auto_eligible
                        "pending",
                    ),
                )
            return "queued"

        # Tier 3: Low confidence - skip
        LOG.debug(
            "Skipping '%s' for %s (confidence=%.2f < %.2f)",
            suggested_action,
            email.gmail_id,
            confidence,
            self.QUEUE_THRESHOLD,
        )
        return "skipped"
