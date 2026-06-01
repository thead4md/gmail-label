from __future__ import annotations

import time
from typing import Optional

from ..storage.database import Database
from ..storage.queries import log_correction, update_sender_profile


def handle_approve(db: Database, queue_id: int) -> bool:
    """Mark a queue item as approved and update sender profile.

    Returns True if the item was found and approved, False if it no longer exists
    (e.g. already processed by another tab — caller should show a warning).
    """
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute("SELECT email_gmail_id FROM action_queue WHERE id = ?", (queue_id,))
        queue_row = cur.fetchone()
        if not queue_row:
            return False

        gmail_id = queue_row['email_gmail_id']
        cur.execute(
            "UPDATE action_queue SET status = 'approved', reviewed_at = ? WHERE id = ?",
            (now, queue_id),
        )

    sender_row = db.execute_sql(
        "SELECT sender FROM emails WHERE gmail_id = ?", (gmail_id,)
    ).fetchone()
    if sender_row and sender_row['sender']:
        update_sender_profile(db, sender_row['sender'], 'approved')

    return True


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
