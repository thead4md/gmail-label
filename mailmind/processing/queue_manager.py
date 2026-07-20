"""Manages the action queue for human-in-the-loop review.

Decides whether to execute, queue, or skip actions based on
prediction confidence scores.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
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
    ) -> Optional[str]:
        """Idempotently enqueue or execute based on a prediction.

        Returns a status string — "executed" | "queued" | "skipped" — or None
        (no-op, e.g. an already-executed/rejected item without force). It does
        NOT return a QueueItem; callers branch on the status string.
        """
        # Use the LLM/ML classification confidence (0-1), NOT total_score/100.
        # total_score is the *priority* score — intentionally 0 for newsletters —
        # and has nothing to do with how certain the classifier is. An explicit
        # confidence (including a legitimate 0.0 from the low-confidence fallback
        # router) is authoritative and must NOT be overridden by the priority
        # score; only a genuinely-absent value falls back for back-compat.
        raw_conf = getattr(prediction, 'confidence', None)
        if raw_conf is None:
            confidence = score_result.total_score / 100.0
        else:
            confidence = float(raw_conf)
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
            fingerprint = make_action_fingerprint(email.gmail_id, suggested_action, {})
            now = int(time.time())

            # CLAIM-BEFORE-EXECUTE (idempotency): insert the executed row FIRST
            # with INSERT OR IGNORE on the UNIQUE action_fingerprint. If a row
            # already exists (rowcount == 0) this email was already auto-actioned,
            # so we must NOT re-execute the Gmail action — return without touching
            # Gmail. Previously a raw INSERT here would both double-execute the
            # action and crash on the unique index when an email was reprocessed.
            with db.transaction() as cur:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO action_queue
                        (email_gmail_id, account, prediction_id, action, params_json, action_fingerprint,
                         status, confidence, priority_score, reason_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        email.gmail_id,
                        # Stamp the account so per-mailbox analytics (autopilot
                        # precision, executed counts, digest) include auto-executed
                        # actions instead of only the account=NULL "all" view.
                        getattr(email, 'account', None),
                        getattr(prediction, 'id', None),
                        suggested_action,
                        json.dumps({}),
                        fingerprint,
                        'executed',
                        confidence,
                        score_result.total_score,
                        json.dumps({'reason': 'auto-executed'}),
                        now,
                        now,
                    ),
                )
                claimed = cur.rowcount != 0
            if not claimed:
                LOG.info(
                    "Auto-action '%s' for %s already recorded; skipping re-execution.",
                    suggested_action, email.gmail_id,
                )
                return "executed"

            # ActionExecutor.execute_action() writes score.primary_label to Gmail
            # (falling back to 'NOTIFICATION' if unset) — it does NOT look at
            # prediction.primary_label. score_result here was reconstructed by the
            # caller from prediction.scoring_breakdown, i.e. the scorer's ORIGINAL
            # pre-resolution label guess; resolve_label_precedence() (pipeline.py)
            # may have since overridden it via ML/LLM/a rule, and THAT resolved
            # label lives on prediction.primary_label. Pass a copy of score_result
            # with primary_label swapped to the resolved label so auto-execute
            # writes the label the pipeline actually decided on — mirroring how
            # feedback.py's _execute_approved_action resolves the label for the
            # human-approval path. Only copy when they actually differ so the
            # common case (no override happened) passes the original object.
            resolved_label = getattr(prediction, 'primary_label', None)
            exec_score_result = score_result
            if resolved_label and resolved_label != score_result.primary_label:
                exec_score_result = replace(score_result, primary_label=resolved_label)

            # Execute the action via executor. Pass the real classification
            # confidence (already verified >= AUTO_EXECUTE_THRESHOLD above), NOT
            # the priority score, so the executor's threshold gate uses the right
            # metric and low-priority actions (e.g. archiving newsletters) are not
            # silently deferred. Respect the return value: a False result means the
            # action was blocked by policy, deferred, or failed at the Gmail API —
            # don't leave a false 'executed' audit row in that case.
            ok = False
            try:
                ok = bool(self.executor.execute_action(
                    email, suggested_action, exec_score_result, confidence=confidence
                ))
            except Exception:
                LOG.exception("Auto-execution failed for %s", email.gmail_id)
                ok = False

            if not ok:
                with db.transaction() as cur:
                    cur.execute(
                        "UPDATE action_queue SET status = 'execute_failed', updated_at = ? "
                        "WHERE action_fingerprint = ?",
                        (int(time.time()), fingerprint),
                    )
                LOG.warning(
                    "Auto-action '%s' for %s did not execute (blocked/deferred/failed); "
                    "recorded as execute_failed.", suggested_action, email.gmail_id,
                )
                return "execute_failed"

            # Record the autopilot action as 'seen' (volume), NOT 'approved'.
            # 'approved' is reserved for HUMAN approvals — counting the system's
            # own auto-executions there would inflate approval_rate / the
            # "trusted" badge with the system's own decisions.
            try:
                from mailmind.storage.queries import update_sender_profile
                update_sender_profile(db, email.sender, 'seen')
            except Exception:
                LOG.warning(
                    "update_sender_profile failed for auto-executed %s",
                    email.gmail_id, exc_info=True,
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
            # Observability (FIX 4): record the skip so dashboard/digest counters
            # are not blind to it — previously this branch wrote nothing to any
            # table. Same INSERT OR IGNORE + action_fingerprint idempotency
            # pattern as the auto-execute path above, so re-processing the same
            # email+action is a no-op instead of a duplicate row or a UNIQUE
            # constraint crash. `action_queue.status` is a plain TEXT column with
            # no CHECK constraint (migrations.py 0008_create_action_queue), so
            # this new status value needs no schema migration. Best-effort: a
            # logging failure here must never change the returned status.
            try:
                skip_fingerprint = make_action_fingerprint(email.gmail_id, suggested_action, {})
                now = int(time.time())
                with db.transaction() as cur:
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO action_queue
                            (email_gmail_id, account, prediction_id, action, params_json, action_fingerprint,
                             status, confidence, priority_score, reason_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            email.gmail_id,
                            getattr(email, 'account', None),
                            getattr(prediction, 'id', None),
                            suggested_action,
                            json.dumps({}),
                            skip_fingerprint,
                            'skipped_low_confidence',
                            confidence,
                            score_result.total_score,
                            json.dumps({'reason': 'skipped_low_confidence'}),
                            now,
                            now,
                        ),
                    )
            except Exception:
                LOG.warning(
                    "Failed to record skipped_low_confidence row for %s",
                    email.gmail_id, exc_info=True,
                )
            return "skipped"

        # Compute unique fingerprint for queueing. Use {} params (same basis as
        # the auto-execute path above) so the same email+action dedupes to one
        # fingerprint regardless of which path enqueued it.
        fingerprint = make_action_fingerprint(email.gmail_id, suggested_action, {})
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
            params=score_result.to_dict(),
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


def filter_now_items(
    items: list,
    queue_threshold: Optional[float] = None,
) -> list:
    """Return items relevant for a "needs attention now" view (formerly the
    dashboard's NOW tab; also used by the FastAPI /api/now route).

    Criteria:
    - reason_json.reply_needed == True, OR
    - priority_score > queue_threshold (stored as int 0-100, threshold as 0.0-1.0)

    Sorted by priority_score DESC, created_at ASC.
    """
    if queue_threshold is None:
        queue_threshold = QueueManager.QUEUE_THRESHOLD

    result = []
    for it in items:
        reason = it.get('reason_json') or it.get('reason') or {}
        if isinstance(reason, str):
            try:
                reason = json.loads(reason)
            except Exception:
                reason = {}
        keep = False
        if reason.get('reply_needed'):
            keep = True
        score = it.get('priority_score')
        if score is not None and score > int(queue_threshold * 100):
            keep = True
        if keep:
            result.append(it)

    result.sort(key=lambda x: (-(x.get('priority_score') or 0), x.get('created_at') or 0))
    return result
