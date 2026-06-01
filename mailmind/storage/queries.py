"""Query helpers for the review dashboard.

All functions accept a ``Database`` instance (from ``mailmind.storage.database``)
and return plain Python dicts suitable for display in Streamlit.
No body_text is ever exposed.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

from mailmind.storage.database import Database


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_prediction_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a prediction row to a safe dict (no body_text)."""
    return {
        "id": row["id"],
        "email_gmail_id": row["email_gmail_id"],
        "model": row["model"],
        "labels": row["labels"],
        "score": row["score"],
        "priority_score": row["priority_score"],
        "confidence": row["confidence"],
        "primary_label": row["primary_label"],
        "pipeline_used": row["pipeline_used"],
        "action_suggested": row["action_suggested"],
        "rule_matches": row["rule_matches"],
        "scoring_breakdown": row["scoring_breakdown"],
        "ml_confidence": row["ml_confidence"],
        "llm_confidence": row["llm_confidence"],
        "created_at": row["created_at"],
    }


def _row_to_action_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert an action row to a safe dict."""
    return {
        "id": row["id"],
        "email_gmail_id": row["email_gmail_id"],
        "action": row["action"],
        "params": row["params"],
        "dry_run": row["dry_run"],
        "succeeded": row["succeeded"],
        "details": row["details"],
        "created_at": row["created_at"],
    }


def _row_to_sender_reputation_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a sender_reputation row to a safe dict."""
    return {
        "sender": row["sender"],
        "score": row["score"],
        "last_seen": row["last_seen"],
    }


# ---------------------------------------------------------------------------
# Public query functions
# ---------------------------------------------------------------------------

def get_recent_predictions(db: Database, limit: int = 50) -> List[Dict[str, Any]]:
    """Return the most recent *limit* predictions as dicts."""
    rows = db.execute_sql(
        "SELECT * FROM predictions ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_prediction_dict(r) for r in rows]


def get_predictions_for_email(db: Database, email_gmail_id: str) -> List[Dict[str, Any]]:
    """Return all predictions for a specific email."""
    rows = db.execute_sql(
        "SELECT * FROM predictions WHERE email_gmail_id = ? ORDER BY created_at DESC",
        (email_gmail_id,),
    ).fetchall()
    return [_row_to_prediction_dict(r) for r in rows]


def get_recent_actions(db: Database, limit: int = 50) -> List[Dict[str, Any]]:
    """Return the most recent *limit* action log entries as dicts."""
    rows = db.execute_sql(
        "SELECT * FROM actions_applied ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_action_dict(r) for r in rows]


def get_sender_reputations(db: Database) -> List[Dict[str, Any]]:
    """Return all sender reputation records as dicts."""
    rows = db.execute_sql(
        "SELECT * FROM sender_reputation ORDER BY score DESC"
    ).fetchall()
    return [_row_to_sender_reputation_dict(r) for r in rows]


def get_summary_metrics(db: Database) -> Dict[str, int]:
    """Return a dict with total counts of emails, predictions, and actions."""
    email_count = db.execute_sql("SELECT COUNT(*) FROM emails").fetchone()[0]
    pred_count = db.execute_sql("SELECT COUNT(*) FROM predictions").fetchone()[0]
    action_count = db.execute_sql("SELECT COUNT(*) FROM actions_applied").fetchone()[0]
    return {
        "emails": email_count,
        "predictions": pred_count,
        "actions": action_count,
    }


# ---------------------------------------------------------------------------
# Action Queue and Sender Profile operations
# ---------------------------------------------------------------------------
import json
import time
from mailmind.storage.models import QueueItem


def get_queue_item_by_fingerprint(db: Database, fingerprint: str) -> QueueItem | None:
    """Fetch a queue item by its action_fingerprint."""
    cur = db._conn.cursor()
    cur.execute("SELECT * FROM action_queue WHERE action_fingerprint = ?", (fingerprint,))
    row = cur.fetchone()
    if not row:
        return None

    def _get(r, name):
        try:
            return r[name]
        except Exception:
            try:
                return getattr(r, name)
            except Exception:
                return None

    params_json = _get(row, 'params_json') or '{}'
    try:
        params = json.loads(params_json)
    except Exception:
        params = {}
    reason_json_raw = _get(row, 'reason_json') or '{}'
    try:
        reason = json.loads(reason_json_raw)
    except Exception:
        reason = {}

    qi = QueueItem(
        id=_get(row, 'id'),
        email_gmail_id=_get(row, 'email_gmail_id'),
        prediction_id=_get(row, 'prediction_id'),
        action=_get(row, 'action') or _get(row, 'suggested_action'),
        params=params,
        action_fingerprint=_get(row, 'action_fingerprint'),
        status=_get(row, 'status'),
        confidence=_get(row, 'confidence') or 0.0,
        priority_score=_get(row, 'priority_score') or 0,
        reason_json=reason,
        created_at=_get(row, 'created_at'),
        updated_at=_get(row, 'updated_at'),
        reviewed_at=_get(row, 'reviewed_at'),
        executed_at=_get(row, 'executed_at'),
    )
    return qi


def upsert_queue_item(db: Database, item: QueueItem) -> QueueItem | None:
    """
    Insert or update a queue item. If new, inserts; if pending, updates metadata; if executed/rejected, no-op.
    """
    now = int(time.time())
    with db.transaction() as cur:
        # Attempt to insert
        cur.execute(
            """
            INSERT OR IGNORE INTO action_queue
                (email_gmail_id, prediction_id, action, params_json, action_fingerprint,
                 status, confidence, priority_score, reason_json, created_at, updated_at, account)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.email_gmail_id,
                item.prediction_id,
                item.action,
                json.dumps(item.params or {}),
                item.action_fingerprint,
                item.status,
                item.confidence,
                item.priority_score,
                json.dumps(item.reason_json or {}),
                item.created_at or now,
                now,
                item.account,
            ),
        )
        # If insertion affected any rows (new item)
        if cur.rowcount:
            return get_queue_item_by_fingerprint(db, item.action_fingerprint)
        # Fetch existing
        existing = get_queue_item_by_fingerprint(db, item.action_fingerprint)
        if not existing:
            return None
        if existing.status == 'pending':
            # refresh metadata
            cur.execute(
                "UPDATE action_queue SET confidence = ?, priority_score = ?, updated_at = ?"
                " WHERE action_fingerprint = ?",
                (item.confidence, item.priority_score, now, item.action_fingerprint),
            )
            return get_queue_item_by_fingerprint(db, item.action_fingerprint)
        if existing.status in ('executed', 'rejected'):
            return None
        # other statuses, return existing
        return existing


def supersede_old_queue_items(db: Database, email_gmail_id: str, keep_fingerprint: str) -> int:
    """
    Mark all other pending items for this email as superseded. Returns count of rows updated.
    """
    with db.transaction() as cur:
        cur.execute(
            "UPDATE action_queue SET status = 'superseded'"
            " WHERE email_gmail_id = ? AND status = 'pending' AND action_fingerprint != ?",
            (email_gmail_id, keep_fingerprint),
        )
        return cur.rowcount


def get_pending_queue(db: Database, limit: int = 50) -> List[Dict[str, Any]]:
    """Return pending items from the action_queue (raw rows)."""
    rows = db.execute_sql(
        "SELECT * FROM action_queue WHERE status = 'pending' ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_corrections(db: Database, limit: int = 50) -> List[Dict[str, Any]]:
    rows = db.execute_sql(
        "SELECT * FROM user_corrections ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Joined prediction+email queries (for dashboard display)
# ---------------------------------------------------------------------------


def _row_to_prediction_with_email_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a joined prediction+email row to a safe dict with subject/sender."""
    return {
        "subject": row["subject"],
        "sender": row["sender"],
        "date": row["date_ts"],
        "preview": row["preview"],
        "primary_label": row["primary_label"],
        "classifier_source": row["classifier_source"],
        "confidence": row["confidence"],
        "llm_rationale": row["llm_rationale"],
        "action_hint": row["action_hint"],
        "email_gmail_id": row["email_gmail_id"],
    }


def get_recent_predictions_with_emails(db: Database, limit: int = 100) -> List[Dict[str, Any]]:
    """Return predictions joined with email metadata, excluding unclassified rows.

    Shows subject, sender, date, a body preview, and prediction info.
    Only returns rows where primary_label is not null.
    """
    rows = db.execute_sql(
        """
        SELECT
            e.subject,
            e.sender,
            e.date_ts,
            SUBSTR(e.body_text, 1, 400) AS preview,
            p.primary_label,
            p.classifier_source,
            p.confidence,
            p.llm_rationale,
            p.llm_action_hint as action_hint,
            p.email_gmail_id
        FROM predictions p
        JOIN emails e ON e.gmail_id = p.email_gmail_id
        WHERE p.primary_label IS NOT NULL
        ORDER BY p.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_row_to_prediction_with_email_dict(r) for r in rows]


def approve_queue_item(db: Database, queue_id: int) -> None:
    """Mark a queue item as approved and set reviewed_at."""
    import time
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            "UPDATE action_queue SET status = 'approved', reviewed_at = ? WHERE id = ?",
            (now, queue_id),
        )


def reject_queue_item(db: Database, queue_id: int) -> None:
    """Mark a queue item as rejected and set reviewed_at."""
    import time
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            "UPDATE action_queue SET status = 'rejected', reviewed_at = ? WHERE id = ?",
            (now, queue_id),
        )


# Add log_correction helper used by feedback
def log_correction(
    db: Database,
    email_gmail_id: str,
    original_label: str | None,
    corrected_label: str | None,
    original_action: str | None = None,
    corrected_action: str | None = None,
    source: str = "dashboard",
):
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO user_corrections
                (email_gmail_id, original_label, corrected_label, original_action, corrected_action, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (email_gmail_id, original_label, corrected_label, original_action, corrected_action, source),
        )


def update_sender_profile(
    db: Database,
    sender_email: str,
    action: str,  # 'approved', 'rejected', or 'seen'
    display_name: str | None = None,
) -> None:
    """Update sender profile stats and recompute trust_tier."""
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO sender_profiles (sender_email, total_seen, total_approved, total_rejected, last_action_ts)"
            " VALUES (?, 0, 0, 0, ?)",
            (sender_email, now),
        )

        if action == 'approved':
            cur.execute(
                "UPDATE sender_profiles"
                " SET total_approved = total_approved + 1, total_seen = total_seen + 1, last_action_ts = ?"
                " WHERE sender_email = ?",
                (now, sender_email),
            )
        elif action == 'rejected':
            cur.execute(
                "UPDATE sender_profiles"
                " SET total_rejected = total_rejected + 1, total_seen = total_seen + 1, last_action_ts = ?"
                " WHERE sender_email = ?",
                (now, sender_email),
            )
        elif action == 'seen':
            cur.execute(
                "UPDATE sender_profiles SET total_seen = total_seen + 1, last_action_ts = ? WHERE sender_email = ?",
                (now, sender_email),
            )

        if display_name:
            cur.execute(
                "UPDATE sender_profiles SET display_name = ? WHERE sender_email = ?",
                (display_name, sender_email),
            )

    # Recompute trust_tier outside the write transaction (read after commit)
    row = db.execute_sql(
        "SELECT total_seen, total_approved, total_rejected FROM sender_profiles WHERE sender_email = ?",
        (sender_email,),
    ).fetchone()
    if row:
        total_seen = row["total_seen"] or 0
        total_approved = row["total_approved"] or 0
        total_rejected = row["total_rejected"] or 0
        total_decided = total_approved + total_rejected
        approval_rate = total_approved / total_decided if total_decided > 0 else 0.0
        rejection_rate = total_rejected / total_decided if total_decided > 0 else 0.0
        if approval_rate > 0.8 and total_seen >= 5:
            tier = "trusted"
        elif rejection_rate > 0.5 and total_seen >= 5:
            tier = "watchlist"
        else:
            tier = "neutral"
        with db.transaction() as cur:
            cur.execute(
                "UPDATE sender_profiles SET trust_tier = ? WHERE sender_email = ?",
                (tier, sender_email),
            )


def get_pending_queue_enriched(db: Database, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Return pending queue items enriched with email and sender info.
    
    Joins action_queue with emails and sender_profiles to provide full context
    for dashboard display.
    """
    rows = db.execute_sql(
        """
        SELECT
            aq.id,
            aq.email_gmail_id,
            aq.prediction_id,
            aq.action,
            aq.params_json,
            aq.action_fingerprint,
            aq.status,
            aq.confidence,
            aq.priority_score,
            aq.reason_json,
            aq.created_at,
            aq.updated_at,
            aq.reviewed_at,
            aq.executed_at,
            e.subject,
            e.sender,
            e.date_ts,
            e.snippet,
            sp.display_name,
            sp.trust_tier,
            sp.total_approved,
            sp.total_rejected,
            sp.auto_action_eligible,
            p.primary_label,
            p.confidence as prediction_confidence,
            p.ml_confidence,
            p.llm_confidence
        FROM action_queue aq
        LEFT JOIN emails e ON e.gmail_id = aq.email_gmail_id
        LEFT JOIN sender_profiles sp ON sp.sender_email = e.sender
        LEFT JOIN predictions p ON p.id = aq.prediction_id
        WHERE aq.status = 'pending'
        ORDER BY aq.priority_score DESC, aq.created_at ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    
    result = []
    for r in rows:
        reason_json_raw = r['reason_json'] or '{}'
        try:
            reason = json.loads(reason_json_raw) if isinstance(reason_json_raw, str) else reason_json_raw
        except Exception:
            reason = {}
         
        result.append({
            'id': r['id'],
            'email_gmail_id': r['email_gmail_id'],
            'prediction_id': r['prediction_id'],
            'action': r['action'],
            'status': r['status'],
            'confidence': r['confidence'],
            'priority_score': r['priority_score'],
            'reason_json': reason,
            'created_at': r['created_at'],
            'updated_at': r['updated_at'],
            'reviewed_at': r['reviewed_at'],
            'executed_at': r['executed_at'],
            'subject': r['subject'],
            'sender': r['sender'],
            'date_ts': r['date_ts'],
            'snippet': r['snippet'],
            'display_name': r['display_name'],
            'trust_tier': r['trust_tier'],
            'total_approved': r['total_approved'],
            'total_rejected': r['total_rejected'],
            'auto_action_eligible': r['auto_action_eligible'],
            'primary_label': r['primary_label'],
            'prediction_confidence': r['prediction_confidence'],
            'ml_confidence': r['ml_confidence'],
            'llm_confidence': r['llm_confidence'],
        })
    
    return result


def get_sender_profiles(db: Database) -> List[Dict[str, Any]]:
    """Return all sender profiles as dicts for dashboard display."""
    rows = db.execute_sql(
        """
        SELECT
            sender_email,
            display_name,
            total_seen,
            total_approved,
            total_rejected,
            last_action_ts,
            trust_tier,
            auto_action_eligible
        FROM sender_profiles
        ORDER BY total_approved DESC
        """
    ).fetchall()
    
    result = []
    for r in rows:
        total_all = (r['total_approved'] or 0) + (r['total_rejected'] or 0)
        approval_rate = 0.0
        if total_all > 0:
            approval_rate = (r['total_approved'] or 0) / total_all
        
        result.append({
            'sender_email': r['sender_email'],
            'display_name': r['display_name'],
            'total_seen': r['total_seen'] or 0,
            'total_approved': r['total_approved'] or 0,
            'total_rejected': r['total_rejected'] or 0,
            'approval_rate': round(approval_rate, 3),
            'trust_tier': r['trust_tier'] or 'neutral',
            'auto_action_eligible': bool(r['auto_action_eligible']),
        })
    
    return result


def toggle_sender_auto_action(db: Database, sender_email: str, enabled: bool) -> None:
    """Toggle auto-action eligibility for a sender."""
    with db.transaction() as cur:
        cur.execute(
            "UPDATE sender_profiles SET auto_action_eligible = ? WHERE sender_email = ?",
            (int(bool(enabled)), sender_email),
        )


def get_queue_stats(db: Database) -> Dict[str, int]:
    """Return queue statistics by status."""
    rows = db.execute_sql(
        """
        SELECT status, COUNT(*) as count
        FROM action_queue
        GROUP BY status
        """
    ).fetchall()
    
    stats = {
        'pending': 0,
        'approved': 0,
        'rejected': 0,
        'superseded': 0,
        'executed': 0,
        'failed': 0,
    }
    
    for r in rows:
        if r['status'] in stats:
            stats[r['status']] = r['count']
    
    # Count items with reply_needed still pending
    reply_needed_count = 0
    pending_rows = db.execute_sql(
        "SELECT reason_json FROM action_queue WHERE status = 'pending'"
    ).fetchall()
    for r in pending_rows:
        try:
            reason = json.loads(r['reason_json'] or '{}')
            if reason.get('reply_needed'):
                reply_needed_count += 1
        except Exception:
            pass
    
    stats['reply_needed_pending'] = reply_needed_count
    
    return stats


def get_ml_model_metadata(db: Database) -> Dict[str, Any]:
    """Fetch ML model metadata (last trained, accuracy, samples)."""
    try:
        # Try to query ml_model_metadata table if it exists
        result = db.execute_sql(
            """
            SELECT * FROM ml_model_metadata
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if result:
            return dict(result)
    except Exception:
        pass
    return None

