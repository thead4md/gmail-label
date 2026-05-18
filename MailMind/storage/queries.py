"""Query helpers for the review dashboard.

All functions accept a ``Database`` instance (from ``mailmind.storage.database``)
and return plain Python dicts suitable for display in Streamlit.
No body_text is ever exposed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mailmind.storage.database import Database
from mailmind.storage.models import Prediction, ActionApplied, SenderReputation


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_prediction_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a prediction row to a safe dict (no body_text)."""
    return {
        "id": row["id"],
        "gmail_id": row["gmail_id"],
        "pipeline_used": row["pipeline_used"],
        "primary_label": row["primary_label"],
        "score": row["score"],
        "confidence": row["confidence"],
        "rule_matches": row["rule_matches"],
        "ml_label": row["ml_label"],
        "ml_confidence": row["ml_confidence"],
        "llm_label": row["llm_label"],
        "llm_confidence": row["llm_confidence"],
        "created_at": row["created_at"],
    }


def _row_to_action_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert an action row to a safe dict."""
    return {
        "id": row["id"],
        "gmail_id": row["gmail_id"],
        "action": row["action"],
        "status": row["status"],
        "error": row["error"],
        "created_at": row["created_at"],
    }


def _row_to_sender_reputation_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a sender_reputation row to a safe dict."""
    return {
        "sender": row["sender"],
        "score": row["score"],
        "updated_at": row["updated_at"],
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


def get_predictions_for_email(db: Database, gmail_id: str) -> List[Dict[str, Any]]:
    """Return all predictions for a specific email."""
    rows = db.execute_sql(
        "SELECT * FROM predictions WHERE gmail_id = ? ORDER BY created_at DESC",
        (gmail_id,),
    ).fetchall()
    return [_row_to_prediction_dict(r) for r in rows]


def get_recent_actions(db: Database, limit: int = 50) -> List[Dict[str, Any]]:
    """Return the most recent *limit* action log entries as dicts."""
    rows = db.execute_sql(
        "SELECT * FROM actions ORDER BY created_at DESC LIMIT ?",
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
    action_count = db.execute_sql("SELECT COUNT(*) FROM actions").fetchone()[0]
    return {
        "emails": email_count,
        "predictions": pred_count,
        "actions": action_count,
    }
