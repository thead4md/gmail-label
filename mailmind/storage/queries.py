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
# Action Queue queries
# ---------------------------------------------------------------------------

def _row_to_queue_item_dict(row):
    """Convert an action_queue row to a safe dict."""
    return {
        "id": row["id"],
        "email_gmail_id": row["email_gmail_id"],
        "prediction_id": row["prediction_id"],
        "suggested_action": row["suggested_action"],
        "primary_label": row["primary_label"],
        "confidence": row["confidence"],
        "auto_eligible": row["auto_eligible"],
        "status": row["status"],
        "reviewed_at": row["reviewed_at"],
        "created_at": row["created_at"],
    }


def get_pending_queue(db, limit=100):
    """Return pending items from the action queue."""
    rows = db.execute_sql(
        "SELECT * FROM action_queue WHERE status = 'pending' ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_queue_item_dict(r) for r in rows]


def approve_queue_item(db, queue_id):
    """Mark a queue item as approved."""
    import time
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            "UPDATE action_queue SET status = 'approved', reviewed_at = ? WHERE id = ?",
            (now, queue_id),
        )


def reject_queue_item(db, queue_id):
    """Mark a queue item as rejected."""
    import time
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            "UPDATE action_queue SET status = 'rejected', reviewed_at = ? WHERE id = ?",
            (now, queue_id),
        )


def log_correction(
    db,
    email_gmail_id,
    original_label,
    corrected_label,
    original_action=None,
    corrected_action=None,
    source="review_dashboard",
):
    """Log a user correction."""
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO user_corrections
                (email_gmail_id, original_label, corrected_label,
                 original_action, corrected_action, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                email_gmail_id,
                original_label,
                corrected_label,
                original_action,
                corrected_action,
                source,
            ),
        )


def get_recent_corrections(db, limit=50):
    """Return the most recent user corrections."""
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
