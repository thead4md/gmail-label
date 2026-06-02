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


def get_pending_queue_enriched(
    db: Database, limit: int = 100, account: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Return pending queue items enriched with email and sender info.

    Joins action_queue with emails and sender_profiles to provide full context
    for dashboard display. When *account* is given, only that mailbox's items
    are returned; otherwise all accounts.
    """
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
        'failed': 0,
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
    """Return distinct senders with NO approve/reject history yet.

    A sender is 'new' if they have no sender_profiles row, or a row with
    zero recorded decisions (total_approved + total_rejected == 0).
    Scoped to pending queue items so the screening list stays actionable.
    """
    account_clause = " AND aq.account = ?" if account else ""
    params: tuple = (account,) if account else ()
    rows = db.execute_sql(
        f"""
        SELECT DISTINCT e.sender               AS sender,
                        COUNT(aq.id)            AS pending_count,
                        MAX(aq.created_at)      AS last_seen
        FROM action_queue aq
        JOIN emails e ON e.gmail_id = aq.email_gmail_id
        LEFT JOIN sender_profiles sp ON sp.sender_email = e.sender
        WHERE aq.status = 'pending'{account_clause}
          AND (sp.sender_email IS NULL
               OR (COALESCE(sp.total_approved,0) + COALESCE(sp.total_rejected,0)) = 0)
        GROUP BY e.sender
        ORDER BY last_seen DESC
        """,
        params if params else None,
    ).fetchall()
    return [
        {"sender": r["sender"], "pending_count": r["pending_count"],
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
            "(sender_email, total_seen, total_approved, total_rejected, last_action_ts, trust_tier) "
            "VALUES (?, 0, 0, 0, ?, ?)",
            (sender_email, now, tier),
        )
        cur.execute(
            "UPDATE sender_profiles SET trust_tier = ?, last_action_ts = ? WHERE sender_email = ?",
            (tier, now, sender_email),
        )


# ---------------------------------------------------------------------------
# Analytics queries for INSIGHTS tab
# ---------------------------------------------------------------------------


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
    acc = " AND aq.account = ?" if account else ""
    p = (since_ts, account, limit) if account else (since_ts, limit)
    rows = db.execute_sql(
        f"""SELECT e.sender AS sender, COUNT(*) AS volume,
                   SUM(CASE WHEN aq.status IN ('approved','executed') THEN 1 ELSE 0 END) AS approved
            FROM action_queue aq JOIN emails e ON e.gmail_id = aq.email_gmail_id
            WHERE aq.created_at >= ?{acc}
            GROUP BY e.sender ORDER BY volume DESC LIMIT ?""", p).fetchall()
    out = []
    for r in rows:
        vol = r["volume"] or 0
        appr = r["approved"] or 0
        out.append({"sender": r["sender"], "volume": vol,
                    "approval_rate": round(appr / vol, 3) if vol else 0.0})
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
