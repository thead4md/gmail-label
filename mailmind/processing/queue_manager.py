"""Manages the action queue for human-in-the-loop review.

Decides whether to execute, queue, or skip actions based on
prediction confidence scores.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

from mailmind.utils.fingerprint import make_action_fingerprint
from mailmind.storage.models import QueueItem
from mailmind.storage.queries import (
    get_queue_item_by_fingerprint,
    is_sender_auto_action_eligible,
    upsert_queue_item,
    supersede_old_queue_items,
)
from ..intelligence.explainer import build_reason_payload
import json

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
        force: bool = False,
    ) -> Optional[QueueItem]:
        """Idempotently enqueue or execute based on prediction. Returns QueueItem or None."""
        confidence = score_result.total_score / 100.0
        suggested_action = prediction.action_suggested

        # Tier 1: Auto-execute
        if not suggested_action:
            LOG.debug("No suggested action for %s; skipping queue.", email.gmail_id)
            return "skipped"

        # EARNED AUTOPILOT (P2B): auto-execute requires BOTH the 0.90 confidence
        # floor AND an explicit per-sender authorisation. Confidence alone is no
        # longer sufficient — see Decisions Log in CONTEXT.md. Senders without
        # `sender_profiles.auto_action_eligible` fall through to the review
        # queue regardless of how confident the model is.
        sender_eligible = is_sender_auto_action_eligible(db, email.sender)
        if confidence >= self.AUTO_EXECUTE_THRESHOLD and sender_eligible:
            LOG.info(
                "Auto-executing '%s' for %s (sender %s eligible, confidence=%.2f >= %.2f)",
                suggested_action,
                email.gmail_id,
                email.sender,
                confidence,
                self.AUTO_EXECUTE_THRESHOLD,
            )
            if getattr(prediction, 'id', None) is None:

                # Fetch predictions from DB to get an ID
                rows = db.get_predictions_for_email(email.gmail_id)
                if rows:
                    # Set prediction.id for consistency
                    setattr(prediction, 'id', rows[0]['id'])
                else:
                    # No prediction found, skip execution
                    return 'skipped'
            # Execute the action via executor
            try:
                self.executor.execute_action(email, suggested_action, score_result)
            except Exception:
                LOG.exception("Auto-execution failed for %s", email.gmail_id)
            # Log executed row
            with db.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO action_queue
                        (email_gmail_id, prediction_id, action, params_json, action_fingerprint,
                         status, confidence, priority_score, reason_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        email.gmail_id,
                        getattr(prediction, 'id', None),
                        suggested_action,
                        json.dumps({}),
                        make_action_fingerprint(email.gmail_id, suggested_action, {}),
                        'executed',
                        confidence,
                        score_result.total_score,
                        json.dumps({'reason': 'auto-executed'}),
                        int(time.time()),
                        int(time.time()),
                    ),
                )
            return "executed"

        # Tier 3: Low confidence - skip
        if confidence < self.QUEUE_THRESHOLD:
            LOG.debug(
                "Skipping '%s' for %s (confidence=%.2f < %.2f)",
                suggested_action,
                email.gmail_id,
                confidence,
                self.QUEUE_THRESHOLD,
            )
            return "skipped"

        # Compute unique fingerprint for queueing
        fingerprint = make_action_fingerprint(email.gmail_id, suggested_action, score_result.details if hasattr(score_result, 'details') else {})
        LOG.debug("Computed fingerprint %s for action %s on email %s", fingerprint, suggested_action, email.gmail_id)
        # Check existing queue item
        existing = get_queue_item_by_fingerprint(db, fingerprint)
        if existing:
            LOG.debug("Found existing queue item with status %s", existing.status)
            if existing.status == 'pending':
                # refresh metadata
                existing.confidence = confidence
                existing.priority_score = score_result.total_score
                existing.updated_at = int(time.time())
                upsert_queue_item(db, existing)
                return "queued"
            if existing.status in ('executed', 'rejected'):
                if not force:
                    LOG.debug("Existing item status %s and force=%s, no-op", existing.status, force)
                    return None
                # force override: delete existing to allow fresh insert
                LOG.debug("Force re-queuing: deleting existing item with fingerprint %s", fingerprint)
                with db.transaction() as cur:
                    cur.execute("DELETE FROM action_queue WHERE action_fingerprint = ?", (fingerprint,))
                existing = None

        # Build new queue item
        # Build reason payload from prediction and thread context
        try:
            thread_ctx = None
            if hasattr(prediction, 'thread_context_json') and prediction.thread_context_json:
                try:
                    thread_ctx = json.loads(prediction.thread_context_json)
                except Exception:
                    thread_ctx = None
            reason_payload = build_reason_payload(db, prediction, thread_ctx)
            reason_json_obj = json.loads(reason_payload.to_json())
        except Exception:
            reason_json_obj = {'primary_label': getattr(prediction, 'primary_label', None)}

        item = QueueItem(
            email_gmail_id=email.gmail_id,
            account=getattr(email, 'account', None),
            prediction_id=getattr(prediction, 'id', None),
            action=suggested_action,
            params=score_result.details if hasattr(score_result, 'details') else {},
            action_fingerprint=fingerprint,
            status='pending',
            confidence=confidence,
            priority_score=score_result.total_score,
            reason_json=reason_json_obj,
        )
        LOG.debug("Upserting new queue item %s", item)
        result = upsert_queue_item(db, item)
        # Supersede old pending items
        count = supersede_old_queue_items(db, email.gmail_id, fingerprint)
        LOG.debug("Superseded %d old items for email %s", count, email.gmail_id)
        return "queued" if result else None

    def execute_action(self, email: "Email", action: str, score_result: "ScoreResult"):
        """Execute an action on an email.

        Args:
            email: The email being processed.
            action: The action to execute.
            score_result: ScoreResult containing the total score and breakdown.
        """
        self.executor.execute_action(email, action, score_result)
