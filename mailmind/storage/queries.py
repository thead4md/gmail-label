"""Query helpers for the review dashboard.

All functions accept a ``Database`` instance (from ``mailmind.storage.database``)
and return plain Python dicts suitable for display in Streamlit.
No body_text is ever exposed.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

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


def get_pending_queue(
    db: Database, limit: int = 50, account: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return pending items from the action_queue (raw rows).

    When *account* is given, only that mailbox's items are returned.
    """
    account_clause = " AND account = ?" if account else ""
    params: tuple = (account, limit) if account else (limit,)
    rows = db.execute_sql(
        f"SELECT * FROM action_queue WHERE status = 'pending'{account_clause}"
        " ORDER BY created_at DESC LIMIT ?",
        params,
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
        "channel": row["channel"],
    }


def get_recent_predictions_with_emails(
    db: Database, limit: int = 100, account: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return predictions joined with email metadata, excluding unclassified rows.

    Shows subject, sender, date, a body preview, and prediction info.
    Only returns rows where primary_label is not null. When *account* is given,
    only that mailbox's predictions are returned; otherwise all accounts.
    """
    account_clause = " AND p.account = ?" if account else ""
    params: tuple = (account, limit) if account else (limit,)
    rows = db.execute_sql(
        f"""
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
            p.email_gmail_id,
            p.channel
        FROM predictions p
        JOIN emails e ON e.gmail_id = p.email_gmail_id
        WHERE p.primary_label IS NOT NULL{account_clause}
        ORDER BY p.created_at DESC
        LIMIT ?
        """,
        params,
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
        "SELECT total_seen, total_approved, total_rejected, tier_source "
        "FROM sender_profiles WHERE sender_email = ?",
        (sender_email,),
    ).fetchone()
    # A manually-set tier (Know/Mute) is sticky — never clobber it with the
    # auto-recompute, otherwise an explicit user trust decision silently reverts
    # to 'neutral' on the very next approve/reject (total_seen still < 5).
    if row and (row["tier_source"] or "auto") != "manual":
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


def get_pending_queue_enriched(
    db: Database, limit: Optional[int] = 100, account: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Return pending queue items enriched with email and sender info.

    Joins action_queue with emails and sender_profiles to provide full context
    for dashboard display. When *account* is given, only that mailbox's items
    are returned; otherwise all accounts. ``limit=None`` means no limit
    (SQLite binds NULL as a datatype mismatch, so coalesce to -1 = unbounded).
    """
    limit = -1 if limit is None else limit
    account_clause = " AND aq.account = ?" if account else ""
    params: tuple = (account, limit) if account else (limit,)
    rows = db.execute_sql(
        f"""
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
            p.llm_confidence,
            p.channel
        FROM action_queue aq
        LEFT JOIN emails e ON e.gmail_id = aq.email_gmail_id
        LEFT JOIN sender_profiles sp ON sp.sender_email = e.sender
        LEFT JOIN predictions p ON p.id = aq.prediction_id
        WHERE aq.status = 'pending'{account_clause}
        ORDER BY aq.priority_score DESC, aq.created_at ASC
        LIMIT ?
        """,
        params,
    ).fetchall()

    result = []
    for r in rows:
        reason_json_raw = r['reason_json'] or '{}'
        try:
            reason = json.loads(reason_json_raw) if isinstance(reason_json_raw, str) else reason_json_raw
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Corrupt reason_json for queue row %s; review context will be empty",
                r['id'] if 'id' in r.keys() else '?', exc_info=True,
            )
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
            'channel': r['channel'],
        })
    
    return result


def get_executed_queue_enriched(
    db: Database, limit: int = 100, account: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return executed/approved/failed queue items enriched with email and sender info.
    was_auto=True means autopilot fired without human review.
    """
    account_clause = " AND aq.account = ?" if account else ""
    params: tuple = (account, limit) if account else (limit,)
    rows = db.execute_sql(
        f"""
        SELECT
            aq.id,
            aq.email_gmail_id,
            aq.action,
            aq.status,
            aq.confidence,
            aq.priority_score,
            aq.reason_json,
            aq.created_at,
            aq.reviewed_at,
            aq.executed_at,
            CASE
                WHEN aq.reviewed_at IS NULL AND aq.status = 'executed' THEN 1
                ELSE 0
            END AS was_auto,
            e.subject,
            e.sender,
            e.date_ts,
            e.snippet,
            sp.trust_tier,
            p.primary_label,
            p.confidence AS prediction_confidence,
            p.ml_confidence,
            p.llm_confidence,
            p.channel
        FROM action_queue aq
        LEFT JOIN emails e ON e.gmail_id = aq.email_gmail_id
        LEFT JOIN sender_profiles sp ON sp.sender_email = e.sender
        LEFT JOIN predictions p ON p.id = aq.prediction_id
        WHERE aq.status IN ('executed', 'approved', 'execute_failed'){account_clause}
        ORDER BY COALESCE(aq.executed_at, aq.reviewed_at, aq.created_at) DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    result = []
    for r in rows:
        reason_json_raw = r['reason_json'] or '{}'
        try:
            reason = json.loads(reason_json_raw) if isinstance(reason_json_raw, str) else reason_json_raw
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Corrupt reason_json for queue row %s; review context will be empty",
                r['id'] if 'id' in r.keys() else '?', exc_info=True,
            )
            reason = {}
        result.append({
            'id': r['id'],
            'email_gmail_id': r['email_gmail_id'],
            'action': r['action'],
            'status': r['status'],
            'confidence': r['confidence'],
            'priority_score': r['priority_score'],
            'reason_json': reason,
            'created_at': r['created_at'],
            'reviewed_at': r['reviewed_at'],
            'executed_at': r['executed_at'],
            'was_auto': bool(r['was_auto']),
            'subject': r['subject'],
            'sender': r['sender'],
            'snippet': r['snippet'],
            'trust_tier': r['trust_tier'],
            'primary_label': r['primary_label'],
            'prediction_confidence': r['prediction_confidence'],
            'ml_confidence': r['ml_confidence'],
            'llm_confidence': r['llm_confidence'],
            'channel': r['channel'],
        })
    return result


def get_sender_profiles(db: Database, coverage: float = 0.75) -> List[Dict[str, Any]]:
    """Return sender profiles covering `coverage` fraction of email volume.

    Merges existing sender_profiles rows with high-volume senders from the
    emails table who don't have a profile yet. Senders are ordered by email
    count DESC; we include enough to cover `coverage` of total messages so
    one-off senders are excluded without needing an arbitrary hard limit.
    """
    rows = db.execute_sql(
        """
        WITH counts AS (
            SELECT sender, COUNT(*) AS email_count
            FROM emails
            WHERE sender IS NOT NULL AND sender != ''
            GROUP BY sender
        ),
        ranked AS (
            SELECT
                sender,
                email_count,
                SUM(email_count) OVER (
                    ORDER BY email_count DESC
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS cumulative,
                SUM(email_count) OVER () AS grand_total
            FROM counts
        )
        SELECT
            r.sender               AS sender_email,
            sp.display_name,
            COALESCE(sp.total_approved, 0)       AS total_approved,
            COALESCE(sp.total_rejected, 0)       AS total_rejected,
            sp.last_action_ts,
            COALESCE(sp.trust_tier, 'neutral')   AS trust_tier,
            COALESCE(sp.auto_action_eligible, 0) AS auto_action_eligible,
            r.email_count
        FROM ranked r
        LEFT JOIN sender_profiles sp ON sp.sender_email = r.sender
        WHERE (r.cumulative - r.email_count) < r.grand_total * ?
        ORDER BY r.email_count DESC
        """,
        (coverage,),
    ).fetchall()

    result = []
    for r in rows:
        total_all = (r['total_approved'] or 0) + (r['total_rejected'] or 0)
        approval_rate = round((r['total_approved'] or 0) / total_all, 3) if total_all else 0.0
        result.append({
            'sender_email':         r['sender_email'],
            'display_name':         r['display_name'],
            'total_approved':       r['total_approved'] or 0,
            'total_rejected':       r['total_rejected'] or 0,
            'approval_rate':        approval_rate,
            'trust_tier':           r['trust_tier'] or 'neutral',
            'auto_action_eligible': bool(r['auto_action_eligible']),
            'email_count':          r['email_count'] or 0,
        })

    return result


def toggle_sender_auto_action(db: Database, sender_email: str, enabled: bool) -> None:
    """Toggle auto-action eligibility for a sender, creating the row if absent."""
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO sender_profiles (sender_email, last_action_ts) VALUES (?, ?)",
            (sender_email, now),
        )
        cur.execute(
            "UPDATE sender_profiles SET auto_action_eligible = ? WHERE sender_email = ?",
            (int(bool(enabled)), sender_email),
        )


def is_sender_auto_action_eligible(db: Database, sender_email: Optional[str]) -> bool:
    """Return True iff the sender has been explicitly granted auto-execute.

    The default for any sender (no profile or auto_action_eligible=0) is
    False — earned autopilot: trust is opt-in per sender, not the default.
    """
    if not sender_email:
        return False
    row = db.execute_sql(
        "SELECT auto_action_eligible FROM sender_profiles WHERE sender_email = ?",
        (sender_email,),
    ).fetchone()
    if row is None:
        return False
    return bool(row["auto_action_eligible"])


def get_queue_stats(db: Database, account: Optional[str] = None) -> Dict[str, int]:
    """Return queue statistics by status.

    When *account* is given, only that mailbox's queue items are counted.
    """
    account_clause = " WHERE account = ?" if account else ""
    params: tuple = (account,) if account else ()
    rows = db.execute_sql(
        f"""
        SELECT status, COUNT(*) as count
        FROM action_queue{account_clause}
        GROUP BY status
        """,
        params,
    ).fetchall()

    stats = {
        'pending': 0,
        'approved': 0,
        'rejected': 0,
        'superseded': 0,
        'executed': 0,
        # Key must match the actual status string used everywhere else
        # ('execute_failed'); the old 'failed' key never matched, so failed
        # executions were silently counted as 0.
        'execute_failed': 0,
    }

    for r in rows:
        if r['status'] in stats:
            stats[r['status']] = r['count']

    # Count items with reply_needed still pending
    reply_needed_count = 0
    reply_clause = " AND account = ?" if account else ""
    pending_rows = db.execute_sql(
        f"SELECT reason_json FROM action_queue WHERE status = 'pending'{reply_clause}",
        params,
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


def build_digest(
    db: Database,
    *,
    since_ts: int,
    account: Optional[str] = None,
) -> Dict[str, Any]:
    """Summarize what MailMind did between ``since_ts`` and now.

    Returns a dict with concrete counters the dashboard and CLI can render
    without any further query gymnastics. When ``account`` is given, all
    counts scope to that mailbox; otherwise they're across every account.

    Keys returned (all integers unless noted):
      since_ts (int): the window start passed in.
      classified (int): predictions written in window.
      executed (int): queue rows that fired against Gmail.
      execute_failed (int): queue rows that tried and failed.
      queued (int): queue rows currently awaiting review.
      pending_reply_needed (int): pending items the LLM flagged reply-needed.
      corrections (int): user_corrections rows logged in window.
      top_labels (list[dict]): top 5 [{label, count}] over the window.
    """
    account_pred = " AND account = ?" if account else ""
    account_q = " AND account = ?" if account else ""
    pred_params = (since_ts, account) if account else (since_ts,)
    q_params_since = (since_ts, account) if account else (since_ts,)

    classified = db.execute_sql(
        f"SELECT COUNT(*) c FROM predictions WHERE created_at >= ?{account_pred}",
        pred_params,
    ).fetchone()["c"]

    executed = db.execute_sql(
        f"SELECT COUNT(*) c FROM action_queue WHERE status = 'executed' "
        f"AND COALESCE(executed_at, updated_at) >= ?{account_q}",
        q_params_since,
    ).fetchone()["c"]

    execute_failed = db.execute_sql(
        f"SELECT COUNT(*) c FROM action_queue WHERE status = 'execute_failed' "
        f"AND updated_at >= ?{account_q}",
        q_params_since,
    ).fetchone()["c"]

    # Queue snapshots (current state, not window-scoped — it's "right now").
    q_params_account = (account,) if account else ()
    account_clause_only = " AND account = ?" if account else ""
    queued = db.execute_sql(
        f"SELECT COUNT(*) c FROM action_queue WHERE status = 'pending'{account_clause_only}",
        q_params_account,
    ).fetchone()["c"]

    reply_needed = 0
    pending_rows = db.execute_sql(
        f"SELECT reason_json FROM action_queue WHERE status = 'pending'{account_clause_only}",
        q_params_account,
    ).fetchall()
    for r in pending_rows:
        try:
            reason = json.loads(r["reason_json"] or "{}")
            if reason.get("reply_needed"):
                reply_needed += 1
        except Exception:
            pass

    # user_corrections has no account column (sender data is shared).
    corrections = db.execute_sql(
        "SELECT COUNT(*) c FROM user_corrections WHERE created_at >= ?",
        (since_ts,),
    ).fetchone()["c"]

    top_label_rows = db.execute_sql(
        f"SELECT primary_label AS label, COUNT(*) AS count "
        f"FROM predictions "
        f"WHERE created_at >= ? AND primary_label IS NOT NULL "
        f"AND primary_label != ''{account_pred} "
        f"GROUP BY primary_label ORDER BY count DESC LIMIT 5",
        pred_params,
    ).fetchall()
    top_labels = [{"label": r["label"], "count": r["count"]} for r in top_label_rows]

    return {
        "since_ts": since_ts,
        "classified": classified,
        "executed": executed,
        "execute_failed": execute_failed,
        "queued": queued,
        "pending_reply_needed": reply_needed,
        "corrections": corrections,
        "top_labels": top_labels,
    }


def get_ml_model_metadata(db: Database) -> Optional[Dict[str, Any]]:
    """Fetch ML model metadata for the dashboard's Model Health panel.

    Training writes metadata to ``system_state`` with keys like
    ``ml_model:pass4_baseline.joblib`` (see ml.train._save_model_metadata_to_db).
    This reader returns a dict shaped for the dashboard:
      created_at:        unix ts of the last training run (from system_state.updated_at)
      training_samples:  num_samples from the saved ModelMetadata
      accuracy:          accuracy from the saved ModelMetadata (may be None)
      class_names:       class list
      version:           model version
    Returns None when no model metadata exists.
    """
    try:
        row = db.execute_sql(
            "SELECT value, updated_at FROM system_state "
            "WHERE key LIKE 'ml_model:%' "
            "ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        meta = json.loads(row["value"] or "{}")
    except Exception:
        meta = {}
    return {
        "created_at": row["updated_at"],
        "training_samples": meta.get("num_samples"),
        "accuracy": meta.get("accuracy"),
        "class_names": meta.get("class_names", []),
        "version": meta.get("version"),
    }


def get_new_senders(db: Database, account: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return distinct senders that have not been triaged yet.

    Sources from predictions (not action_queue) so emails that were classified
    but never queued still surface here. A sender is 'new' only while they have
    neither recorded approve/reject decisions NOR an explicit trust tier — the
    Know/Mute/Block buttons all assign one via set_sender_trust_tier(). Honouring
    trust_tier here means triaging a sender removes them from this list for good,
    not just for the current dashboard session (which was only masking them via
    ephemeral st.session_state["dismissed_senders"]).
    """
    account_clause = " AND p.account = ?" if account else ""
    params: tuple = (account,) if account else ()
    rows = db.execute_sql(
        f"""
        SELECT DISTINCT e.sender               AS sender,
                        COUNT(p.id)            AS email_count,
                        MAX(p.created_at)      AS last_seen
        FROM predictions p
        JOIN emails e ON e.gmail_id = p.email_gmail_id
        LEFT JOIN sender_profiles sp ON sp.sender_email = e.sender
        WHERE e.sender IS NOT NULL{account_clause}
          AND (sp.sender_email IS NULL
               OR ((COALESCE(sp.total_approved,0) + COALESCE(sp.total_rejected,0)) = 0
                   AND COALESCE(sp.trust_tier,'neutral') = 'neutral'))
        GROUP BY e.sender
        ORDER BY last_seen DESC
        LIMIT 50
        """,
        params if params else None,
    ).fetchall()
    return [
        {"sender": r["sender"], "email_count": r["email_count"],
         "last_seen": r["last_seen"]}
        for r in rows if r["sender"]
    ]


def set_sender_trust_tier(db: Database, sender_email: str, tier: str) -> None:
    """Force a sender's trust_tier directly (used by new-sender screening).

    tier must be one of: 'trusted', 'neutral', 'watchlist'.
    Creates the profile row if absent.
    """
    if tier not in ("trusted", "neutral", "watchlist"):
        raise ValueError(f"invalid tier: {tier}")
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO sender_profiles "
            "(sender_email, total_seen, total_approved, total_rejected, last_action_ts, trust_tier, tier_source) "
            "VALUES (?, 0, 0, 0, ?, ?, 'manual')",
            (sender_email, now, tier),
        )
        # tier_source='manual' so the next approve/reject recompute in
        # update_sender_profile won't silently revert this explicit user choice.
        cur.execute(
            "UPDATE sender_profiles SET trust_tier = ?, tier_source = 'manual', last_action_ts = ? "
            "WHERE sender_email = ?",
            (tier, now, sender_email),
        )


# ---------------------------------------------------------------------------
# Sender & thread label rules
# ---------------------------------------------------------------------------


def set_sender_label_rule(db: Database, sender_email: str, label: str,
                          account: Optional[str] = None,
                          match_pattern: Optional[str] = None) -> None:
    """Create or replace a sender label rule.

    ``match_pattern`` is an optional regex tested (case-insensitive) against the
    subject. When None the rule is a catch-all that labels every message from the
    sender; when set the rule only fires for matching subjects, letting one
    sender (e.g. a listserv) map to several labels by content. The composite PK
    (sender_email, label, account) means each distinct label is its own row, so a
    sender can carry many conditional rules plus an optional catch-all.
    """
    now = int(time.time())
    pat = (match_pattern or "").strip() or None
    with db.transaction() as cur:
        # Delete-then-insert rather than INSERT OR REPLACE: SQLite treats NULLs as
        # distinct in a UNIQUE/PK index, so OR REPLACE never matches the common
        # account IS NULL (single-mailbox) case and would accumulate duplicate
        # rules. `account IS ?` matches NULL, so this dedupes correctly for both.
        cur.execute(
            "DELETE FROM sender_label_rules "
            "WHERE sender_email = ? AND label = ? AND account IS ?",
            (sender_email, label, account),
        )
        cur.execute(
            "INSERT INTO sender_label_rules "
            "(sender_email, label, account, match_pattern, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sender_email, label, account, pat, now),
        )


def set_thread_label_rule(db: Database, thread_id: str, label: str) -> None:
    """Create or replace a rule: always label this thread with label."""
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO thread_label_rules "
            "(thread_id, label, created_at) "
            "VALUES (?, ?, ?)",
            (thread_id, label, now),
        )


def get_sender_label(db: Database, sender_email: str, account: Optional[str] = None) -> Optional[str]:
    """Return the most-recent label rule for sender_email, or None.

    Back-compat helper: ignores match_pattern and returns the newest rule's label.
    Use resolve_sender_label() for content-aware resolution in the pipeline.
    """
    row = db.execute_sql(
        "SELECT label FROM sender_label_rules "
        "WHERE sender_email = ? AND account IS ? "
        "ORDER BY created_at DESC LIMIT 1",
        (sender_email, account),
    ).fetchone()
    return row["label"] if row else None


def get_sender_rules(db: Database, sender_email: str,
                     account: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return all label rules for a sender as dicts {label, match_pattern}.

    Ordered so conditional (pattern) rules come before the catch-all (NULL
    pattern), newest first within each group — the evaluation order used by
    resolve_sender_label.
    """
    rows = db.execute_sql(
        "SELECT label, match_pattern FROM sender_label_rules "
        "WHERE sender_email = ? AND account IS ? "
        "ORDER BY (match_pattern IS NULL), created_at DESC",
        (sender_email, account),
    ).fetchall()
    return [{"label": r["label"], "match_pattern": r["match_pattern"]} for r in rows]


def resolve_sender_label(db: Database, sender_email: str, subject: Optional[str],
                         account: Optional[str] = None) -> Optional[str]:
    """Resolve a sender's rules against a specific subject.

    Conditional (pattern) rules are tested first, case-insensitive, in recency
    order — the first whose regex matches the subject wins. If none match, the
    catch-all rule's label is returned (if any). Returns None when the sender has
    no rule or only conditional rules and none match, so the caller falls through
    to normal content classification. A malformed user regex is skipped, never
    raised — a bad rule must not crash the pipeline.
    """
    import re
    import logging

    rules = get_sender_rules(db, sender_email, account)
    if not rules:
        return None
    text = subject or ""
    catch_all: Optional[str] = None
    for rule in rules:
        pattern = rule.get("match_pattern")
        if not pattern:
            if catch_all is None:
                catch_all = rule["label"]
            continue
        try:
            if re.search(pattern, text, re.IGNORECASE | re.UNICODE):
                return rule["label"]
        except re.error:
            logging.getLogger(__name__).warning(
                "Invalid sender rule pattern %r for %s; skipping", pattern, sender_email
            )
            continue
    return catch_all


def get_thread_label(db: Database, thread_id: str) -> Optional[str]:
    """Return the label rule for thread_id, or None if no rule exists."""
    row = db.execute_sql(
        "SELECT label FROM thread_label_rules "
        "WHERE thread_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (thread_id,),
    ).fetchone()
    return row["label"] if row else None


def get_sender_label_prior(
    db: Database,
    sender_email: str,
    account: Optional[str] = None,
    min_count: int = 3,
) -> dict:
    """Return a normalised label distribution for sender_email based on
    user-confirmed labels (corrections + approvals).

    Returns {} (abstain) when the sender has fewer than min_count confirmed
    observations — the blend then uses pure content for that sender.

    The prior is derived from user *corrections* (ground truth) only, not from
    past model predictions, to avoid echoing the content model back into itself.
    Light additive smoothing (alpha=0.5) is applied after the min_count gate.
    """
    # Bug #4 fix: scope query to the given account so corrections from one mailbox
    # don't bleed into another's sender prior.
    if account is not None:
        account_clause = "AND e.account = ?"
        params: tuple = (sender_email, account)
    else:
        account_clause = ""
        params = (sender_email,)

    rows = db.execute_sql(
        f"""
        SELECT uc.corrected_label AS label, COUNT(*) AS cnt
        FROM user_corrections uc
        JOIN emails e ON e.gmail_id = uc.email_gmail_id
        WHERE e.sender = ?
          {account_clause}
          AND uc.corrected_label IS NOT NULL
          AND uc.corrected_label != ''
        GROUP BY uc.corrected_label
        """,
        params,
    ).fetchall()

    counts: dict = {r["label"]: r["cnt"] for r in rows}
    total = sum(counts.values())
    if total < min_count:
        return {}

    alpha = 0.5
    n_classes = len(counts)
    smoothed = {lbl: (cnt + alpha) / (total + alpha * n_classes)
                for lbl, cnt in counts.items()}
    norm = sum(smoothed.values())
    return {lbl: v / norm for lbl, v in smoothed.items()}


def get_gmail_labels(db: Database, account: Optional[str] = None) -> List[str]:
    """Return the list of Gmail label names for display.

    Used by the dashboard to populate label selection dropdowns.
    Returns just the label names (from gmail_label_map).
    """
    acc = " WHERE account = ?" if account else ""
    params = (account,) if account else ()
    rows = db.execute_sql(
        f"SELECT DISTINCT name FROM gmail_label_map{acc} ORDER BY name",
        params,
    ).fetchall()
    return [r["name"] for r in rows]


# ---------------------------------------------------------------------------
# Analytics queries for INSIGHTS tab
# ---------------------------------------------------------------------------


def record_llm_usage(db: Database, records: List[Dict[str, Any]]) -> None:
    """Persist buffered LLM usage records (from llm_classifier.drain_pending_usage)."""
    if not records:
        return
    with db.transaction() as cur:
        cur.executemany(
            "INSERT INTO llm_usage "
            "(ts, model, kind, prompt_tokens, completion_tokens, cost_usd, latency_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (r.get("ts"), r.get("model"), r.get("kind"),
                 r.get("prompt_tokens", 0), r.get("completion_tokens", 0),
                 r.get("cost_usd", 0.0), r.get("latency_ms", 0))
                for r in records
            ],
        )


def analytics_llm_cost(db: Database, since_ts: int = 0) -> Dict[str, Any]:
    """Aggregate LLM spend since since_ts: totals + a per-model/kind breakdown.

    Global (single-user); the llm_usage table has no account dimension.
    """
    total = db.execute_sql(
        "SELECT COUNT(*) AS calls, COALESCE(SUM(cost_usd),0) AS cost, "
        "COALESCE(SUM(prompt_tokens+completion_tokens),0) AS tokens, "
        "COALESCE(AVG(latency_ms),0) AS avg_latency_ms "
        "FROM llm_usage WHERE ts >= ?",
        (since_ts,),
    ).fetchone()
    by_kind = db.execute_sql(
        "SELECT model, kind, COUNT(*) AS calls, COALESCE(SUM(cost_usd),0) AS cost "
        "FROM llm_usage WHERE ts >= ? GROUP BY model, kind ORDER BY cost DESC",
        (since_ts,),
    ).fetchall()
    return {
        "calls": total["calls"] or 0,
        "cost_usd": round(total["cost"] or 0.0, 6),
        "tokens": total["tokens"] or 0,
        "avg_latency_ms": int(total["avg_latency_ms"] or 0),
        "by_kind": [
            {"model": r["model"], "kind": r["kind"],
             "calls": r["calls"], "cost_usd": round(r["cost"] or 0.0, 6)}
            for r in by_kind
        ],
    }


def analytics_tier_quality(db: Database, since_ts: int = 0,
                           account: Optional[str] = None) -> List[Dict[str, Any]]:
    """Per-classifier-source correction rate.

    For predictions from each tier (rules/ml/llm/fallback/rule), how many did the
    user later correct (corrected_label != the predicted primary_label)? A proxy
    for how often each tier is wrong — the live signal that classification is
    (or isn't) improving. Counts each prediction once (EXISTS avoids join fan-out).
    """
    acc = " AND p.account = ?" if account else ""
    params = (since_ts, account) if account else (since_ts,)
    rows = db.execute_sql(
        f"""
        SELECT source, COUNT(*) AS total, SUM(was_corrected) AS corrected
        FROM (
            SELECT COALESCE(p.classifier_source, 'rules') AS source,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM user_corrections uc
                       WHERE uc.email_gmail_id = p.email_gmail_id
                         AND uc.corrected_label IS NOT NULL
                         AND uc.corrected_label != p.primary_label
                   ) THEN 1 ELSE 0 END AS was_corrected
            FROM predictions p
            WHERE p.created_at >= ?{acc}
        ) GROUP BY source ORDER BY total DESC
        """,
        params,
    ).fetchall()
    out = []
    for r in rows:
        total = r["total"] or 0
        corrected = r["corrected"] or 0
        out.append({
            "source": r["source"],
            "total": total,
            "corrections": corrected,
            "correction_rate": round(corrected / total, 3) if total else 0.0,
        })
    return out


def analytics_autopilot_precision(db: Database, since_ts: int = 0,
                                  account: Optional[str] = None) -> Dict[str, Any]:
    """Of auto-executed actions, how many were later corrected by the user.

    precision = 1 - corrected/executed. None when there are no auto-executions.
    """
    acc = " AND aq.account = ?" if account else ""
    params = (since_ts, account) if account else (since_ts,)
    row = db.execute_sql(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN EXISTS (
                   SELECT 1 FROM user_corrections uc
                   JOIN predictions p ON p.email_gmail_id = aq.email_gmail_id
                   WHERE uc.email_gmail_id = aq.email_gmail_id
                     AND uc.corrected_label IS NOT NULL
                     AND uc.corrected_label != p.primary_label
               ) THEN 1 ELSE 0 END) AS later_corrected
        FROM action_queue aq
        WHERE aq.status = 'executed'
          AND aq.reason_json LIKE '%auto-executed%'
          AND aq.created_at >= ?{acc}
        """,
        params,
    ).fetchone()
    total = row["total"] or 0
    corrected = row["later_corrected"] or 0
    return {
        "auto_executed": total,
        "later_corrected": corrected,
        "precision": round(1 - corrected / total, 3) if total else None,
    }


def analytics_label_distribution(db: Database, since_ts: int,
                                  account: Optional[str] = None) -> List[Dict[str, Any]]:
    acc = " AND account = ?" if account else ""
    p = (since_ts, account) if account else (since_ts,)
    rows = db.execute_sql(
        f"""SELECT primary_label AS label, COUNT(*) AS count
            FROM predictions
            WHERE created_at >= ? AND primary_label IS NOT NULL{acc}
            GROUP BY primary_label ORDER BY count DESC""", p).fetchall()
    return [{"label": r["label"], "count": r["count"]} for r in rows]


def analytics_channel_distribution(db: Database, since_ts: int,
                                   account: Optional[str] = None) -> List[Dict[str, Any]]:
    acc = " AND account = ?" if account else ""
    p = (since_ts, account) if account else (since_ts,)
    rows = db.execute_sql(
        f"""SELECT COALESCE(channel,'unknown') AS channel, COUNT(*) AS count
            FROM predictions
            WHERE created_at >= ?{acc}
            GROUP BY channel ORDER BY count DESC""", p).fetchall()
    return [{"channel": r["channel"], "count": r["count"]} for r in rows]


def analytics_top_senders(db: Database, since_ts: int, limit: int = 10,
                          account: Optional[str] = None) -> List[Dict[str, Any]]:
    # Source from emails (all received mail), not action_queue (only queued items).
    # Join action_queue LEFT to compute approval_rate where available.
    acc_e = " AND e.account = ?" if account else ""
    p = (since_ts, account, limit) if account else (since_ts, limit)
    rows = db.execute_sql(
        f"""SELECT e.sender AS sender, COUNT(DISTINCT e.id) AS volume,
                   SUM(CASE WHEN aq.status IN ('approved','executed') THEN 1 ELSE 0 END) AS approved,
                   COUNT(aq.id) AS queued
            FROM emails e
            LEFT JOIN action_queue aq ON aq.email_gmail_id = e.gmail_id
            WHERE COALESCE(e.date_ts, e.created_at) >= ?{acc_e}
            GROUP BY e.sender ORDER BY volume DESC LIMIT ?""", p).fetchall()
    out = []
    for r in rows:
        vol = r["volume"] or 0
        appr = r["approved"] or 0
        queued = r["queued"] or 0
        out.append({"sender": r["sender"], "volume": vol,
                    "approval_rate": round(appr / queued, 3) if queued else 0.0})
    return out


def analytics_decision_times(db: Database, since_ts: int,
                             account: Optional[str] = None) -> List[Dict[str, Any]]:
    """Minutes between created_at and reviewed_at for reviewed items."""
    acc = " AND account = ?" if account else ""
    p = (since_ts, account) if account else (since_ts,)
    rows = db.execute_sql(
        f"""SELECT (reviewed_at - created_at) AS secs
            FROM action_queue
            WHERE reviewed_at IS NOT NULL AND created_at >= ?{acc}""", p).fetchall()
    return [{"minutes": round((r["secs"] or 0) / 60.0, 1)} for r in rows if r["secs"] is not None]


def analytics_channel_weekday(db: Database, since_ts: int,
                              account: Optional[str] = None) -> List[Dict[str, Any]]:
    """channel × weekday counts for a heatmap (weekday 0=Sun..6=Sat per SQLite %w)."""
    acc = " AND account = ?" if account else ""
    p = (since_ts, account) if account else (since_ts,)
    rows = db.execute_sql(
        f"""SELECT COALESCE(channel,'unknown') AS channel,
                   CAST(strftime('%w', created_at, 'unixepoch') AS INTEGER) AS weekday,
                   COUNT(*) AS count
            FROM predictions WHERE created_at >= ?{acc}
            GROUP BY channel, weekday""", p).fetchall()
    return [{"channel": r["channel"], "weekday": r["weekday"], "count": r["count"]} for r in rows]


def get_labeled_predictions(
    db: Database, since_ts: int, account: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return (email_gmail_id, primary_label) for classified emails in the window.

    Used by the `apply-labels` command to stamp MailMind's predicted category
    onto the actual Gmail messages. Only rows with a non-empty primary_label.
    """
    acc = " AND account = ?" if account else ""
    params: tuple = (since_ts, account) if account else (since_ts,)
    rows = db.execute_sql(
        f"""SELECT email_gmail_id, primary_label
            FROM predictions
            WHERE created_at >= ? AND primary_label IS NOT NULL
              AND primary_label != ''{acc}""",
        params,
    ).fetchall()
    return [{"email_gmail_id": r["email_gmail_id"], "primary_label": r["primary_label"]}
            for r in rows]
