from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, List
import time
import json

from ..storage.database import Database


@dataclass
class SenderProfileSummary:
    sender_email: str
    display_name: Optional[str]
    total_seen: int
    total_approved: int
    total_rejected: int
    last_action_ts: Optional[int]
    trust_tier: str
    auto_action_eligible: bool


def get_sender_profile(db: Database, sender_email: str) -> Optional[SenderProfileSummary]:
    cur = db.execute_sql("SELECT * FROM sender_profiles WHERE sender_email = ?", (sender_email,))
    row = cur.fetchone()
    if not row:
        return None
    return SenderProfileSummary(
        sender_email=row["sender_email"],
        display_name=row["display_name"],
        total_seen=row["total_seen"],
        total_approved=row["total_approved"],
        total_rejected=row["total_rejected"],
        last_action_ts=row["last_action_ts"],
        trust_tier=row["trust_tier"],
        auto_action_eligible=bool(row["auto_action_eligible"]),
    )


def get_sender_trust_tier(db: Database, sender_email: str) -> str:
    profile = get_sender_profile(db, sender_email)
    if not profile:
        return "neutral"
    return profile.trust_tier or "neutral"


def update_from_outcome(db: Database, sender_email: str, approved: bool) -> None:
    """Update sender profile counts deterministically.

    approved=True increments total_approved; False increments total_rejected.
    """
    outcome = "approved" if approved else "rejected"
    # reuse queries.update_sender_profile if available to keep logic consistent
    from ..storage.queries import update_sender_profile

    update_sender_profile(db, sender_email, outcome)


def get_similar_sender_history(db: Database, sender_email: str, limit: int = 5) -> List[dict]:
    """Return recent action decisions for this sender (lightweight history).

    Joins action_queue and emails to return recent actions for sender.
    """
    rows = db.execute_sql(
        """
        SELECT q.*, e.subject, e.date_ts FROM action_queue q
        LEFT JOIN emails e ON e.gmail_id = q.email_gmail_id
        WHERE e.sender = ? ORDER BY q.created_at DESC LIMIT ?
        """,
        (sender_email, limit),
    ).fetchall()
    results = []
    for r in rows:
        rec = dict(r)
        # parse json fields
        rec["params"] = json.loads(rec.pop("params_json", "{}") or "{}")
        rec["reason_json"] = json.loads(rec.get("reason_json") or "{}")
        results.append(rec)
    return results

