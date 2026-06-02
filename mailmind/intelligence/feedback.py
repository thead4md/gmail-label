from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ..storage.database import Database
from ..storage.models import Email
from ..storage.queries import log_correction, update_sender_profile

LOG = logging.getLogger(__name__)


def handle_approve(
    db: Database,
    queue_id: int,
    executor: Optional[Any] = None,
) -> bool:
    """Approve a queue item — and when an executor is provided, run the action.

    Pillar 2A: before this, the dashboard's Approve button merely flipped the
    queue row's status, and nothing ever applied approved actions to Gmail.
    Now an optional executor is invoked to actually perform the action, and
    the row's status reflects what happened (executed | execute_failed |
    approved when no executor was passed = legacy back-compat).

    Returns True if the item was found, False if it no longer exists (e.g.
    already processed by another tab — caller should show a warning).
    """
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            "SELECT email_gmail_id, action, confidence, priority_score "
            "FROM action_queue WHERE id = ?",
            (queue_id,),
        )
        queue_row = cur.fetchone()
        if not queue_row:
            return False

        gmail_id = queue_row['email_gmail_id']
        action = queue_row['action']
        confidence = float(queue_row['confidence'] or 0.0)
        priority_score = int(queue_row['priority_score'] or 0)
        # Provisional status — finalised below when executor is provided.
        cur.execute(
            "UPDATE action_queue SET status = 'approved', reviewed_at = ? WHERE id = ?",
            (now, queue_id),
        )

    # Build the Email + ScoreResult the executor needs.
    if executor is not None:
        new_status = _execute_approved_action(
            db, executor, queue_id=queue_id, gmail_id=gmail_id, action=action,
            confidence=confidence, priority_score=priority_score,
        )
        with db.transaction() as cur:
            if new_status == 'executed':
                cur.execute(
                    "UPDATE action_queue SET status = 'executed', executed_at = ? WHERE id = ?",
                    (int(time.time()), queue_id),
                )
            elif new_status == 'execute_failed':
                cur.execute(
                    "UPDATE action_queue SET status = 'execute_failed' WHERE id = ?",
                    (queue_id,),
                )
            # else leave 'approved' for the legacy/no-executor path.

    sender_row = db.execute_sql(
        "SELECT sender FROM emails WHERE gmail_id = ?", (gmail_id,)
    ).fetchone()
    if sender_row and sender_row['sender']:
        update_sender_profile(db, sender_row['sender'], 'approved')

    return True


def _execute_approved_action(
    db: Database,
    executor: Any,
    *,
    queue_id: int,
    gmail_id: str,
    action: str,
    confidence: float,
    priority_score: int,
) -> str:
    """Run the executor for an approved queue item.

    Returns one of: 'executed' | 'execute_failed' | 'approved' (when the
    email or score data couldn't be reconstructed, so we keep the legacy
    'approved' status as an audit trail without ever calling Gmail).
    """
    from ..processing.scorer import ScoreResult  # local: avoid cycle at import

    email_row = db.get_email_by_gmail_id(gmail_id)
    if email_row is None:
        LOG.warning("Approved queue %s references missing email %s — not executing.",
                    queue_id, gmail_id)
        return 'approved'

    # Resurrect a minimal Email from the cached row.
    email = Email(
        gmail_id=email_row['gmail_id'],
        thread_id=email_row['thread_id'],
        sender=email_row['sender'],
        recipients=(email_row['recipients'] or '').split(',') if email_row['recipients'] else [],
        subject=email_row['subject'],
        snippet=email_row['snippet'],
        body_text=email_row['body_text'],
        date_ts=email_row['date_ts'],
        labels=(email_row['labels'] or '').split(',') if email_row['labels'] else [],
        parsed=bool(email_row['parsed']),
    )
    # Look up the most recent primary_label so the executor (and downstream
    # safety policy) knows the category — critical for the auto-archive guard.
    pred_row = db.execute_sql(
        "SELECT primary_label FROM predictions WHERE email_gmail_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (gmail_id,),
    ).fetchone()
    primary_label = pred_row['primary_label'] if pred_row else None

    # Executor reads .total_score and .primary_label off ScoreResult; map the
    # queue row's normalised confidence back to a 0-100 integer for that path.
    score_total = int(round(confidence * 100)) if confidence else priority_score
    try:
        score = ScoreResult(
            total_score=score_total,
            base_score=score_total,
            rule_contribution=0,
            direct_mention_bonus=0,
            recency_bonus=0,
            sender_trust=0,
            primary_label=primary_label,
        )
        ok = executor.execute_action(email, action, score)
    except Exception as exc:
        LOG.error("Executor raised on approval of queue %s: %s", queue_id, exc, exc_info=True)
        return 'execute_failed'
    return 'executed' if ok else 'execute_failed'


def handle_reject(db: Database, queue_id: int, corrected_action: Optional[str] = None) -> bool:
    """Mark a queue item as rejected and update sender profile.

    Returns True if the item was found and rejected, False if it no longer exists.
    """
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute("SELECT email_gmail_id, action FROM action_queue WHERE id = ?", (queue_id,))
        queue_row = cur.fetchone()
        if not queue_row:
            return False

        gmail_id = queue_row['email_gmail_id']
        old_action = queue_row['action']
        cur.execute(
            "UPDATE action_queue SET status = 'rejected', reviewed_at = ? WHERE id = ?",
            (now, queue_id),
        )

    sender_row = db.execute_sql(
        "SELECT sender FROM emails WHERE gmail_id = ?", (gmail_id,)
    ).fetchone()
    if sender_row and sender_row['sender']:
        update_sender_profile(db, sender_row['sender'], 'rejected')

    if corrected_action:
        log_correction(
            db,
            gmail_id,
            original_label=None,
            corrected_label=None,
            original_action=old_action,
            corrected_action=corrected_action,
            source='dashboard',
        )

    return True


def handle_correction(
    db: Database,
    queue_id: int,
    corrected_label: Optional[str] = None,
    corrected_action: Optional[str] = None,
) -> bool:
    """Handle user label/action correction and log it.

    Returns True if the item was found and correction logged, False if not found.
    """
    with db.transaction() as cur:
        cur.execute("SELECT email_gmail_id, action FROM action_queue WHERE id = ?", (queue_id,))
        queue_row = cur.fetchone()
        if not queue_row:
            return False

        gmail_id = queue_row['email_gmail_id']
        original_action = queue_row['action']

    pred_row = db.execute_sql(
        "SELECT primary_label FROM predictions WHERE email_gmail_id = ? ORDER BY created_at DESC LIMIT 1",
        (gmail_id,),
    ).fetchone()
    original_label = pred_row['primary_label'] if pred_row else None

    log_correction(
        db,
        gmail_id,
        original_label=original_label,
        corrected_label=corrected_label,
        original_action=original_action,
        corrected_action=corrected_action,
        source='dashboard_correction',
    )

    return True


def handle_know_sender(db: Database, sender_email: str) -> bool:
    """Mark a sender as trusted (you know them)."""
    from ..storage.queries import set_sender_trust_tier
    if not sender_email:
        return False
    set_sender_trust_tier(db, sender_email, "trusted")
    return True


def handle_mute_sender(db: Database, sender_email: str) -> bool:
    """Mute a sender: watchlist tier (their mail is downranked, not deleted)."""
    from ..storage.queries import set_sender_trust_tier
    if not sender_email:
        return False
    set_sender_trust_tier(db, sender_email, "watchlist")
    return True


def handle_block_sender(db: Database, sender_email: str) -> bool:
    """Block a sender: watchlist tier + reject all their pending queue items."""
    from ..storage.queries import set_sender_trust_tier
    if not sender_email:
        return False
    set_sender_trust_tier(db, sender_email, "watchlist")
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            """UPDATE action_queue SET status = 'rejected', reviewed_at = ?
               WHERE status = 'pending' AND email_gmail_id IN (
                   SELECT gmail_id FROM emails WHERE sender = ?)""",
            (now, sender_email),
        )
    return True
