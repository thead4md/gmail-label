"""Bulk label/archive over arbitrary browsed mail — shared by every UI surface
(previously duplicated inline in dashboard/tab_inbox.py; extracted here so the
FastAPI backend and any future surface use the exact same logic instead of a
third copy).

Unlike the queue-approve/reject path, most emails a user browses/searches
were never queued or predicted at all, so the queue-item-based handlers in
mailmind.intelligence.feedback don't apply. Instead a minimal Email is
resurrected straight from its DB row and driven through
ActionExecutor.execute_action directly with a synthetic, maximally-confident
ScoreResult — execute_action already owns all dry_run/SafetyPolicy gating, so
this module adds no safety logic of its own.
"""
from __future__ import annotations

from typing import Any, Optional

from mailmind.processing.scorer import ScoreResult
from mailmind.storage.database import Database
from mailmind.storage.models import Email


def resolve_email_for_action(db: Database, gmail_id: str) -> Optional[Email]:
    """Resurrect a minimal Email object from its DB row for the executor.

    Mirrors mailmind.intelligence.feedback._execute_approved_action's
    reconstruction byte-for-byte (same fields, same comma-split convention for
    recipients/labels) so the executor and SafetyPolicy see exactly the same
    Email shape they always do.
    """
    email_row = db.get_email_by_gmail_id(gmail_id)
    if email_row is None:
        return None
    return Email(
        gmail_id=email_row["gmail_id"],
        thread_id=email_row["thread_id"],
        sender=email_row["sender"],
        recipients=(email_row["recipients"] or "").split(",") if email_row["recipients"] else [],
        subject=email_row["subject"],
        snippet=email_row["snippet"],
        body_text=email_row["body_text"],
        date_ts=email_row["date_ts"],
        labels=(email_row["labels"] or "").split(",") if email_row["labels"] else [],
        parsed=bool(email_row["parsed"]),
    )


def synthetic_score(primary_label: Optional[str]) -> ScoreResult:
    """Maximally-confident synthetic ScoreResult for a manual bulk action.
    total_score=100 plus the confidence=1.0 passed at the execute_action call
    site clears every CONFIDENCE_THRESHOLDS gate — this is a deliberate,
    direct user action, not a model prediction, so there's no lower
    confidence to reflect."""
    return ScoreResult(
        total_score=100,
        base_score=100,
        rule_contribution=0,
        direct_mention_bonus=0,
        recency_bonus=0,
        sender_trust=0,
        primary_label=primary_label,
    )


def run_bulk_action(
    db: Database,
    executor: Any,
    gmail_id: str,
    action: str,
    current_primary_label: Optional[str],
    chosen_label: Optional[str],
) -> bool:
    """Apply `action` ('label' or 'archive') to one email. Returns whether it
    succeeded (False also covers "email not found").

    For 'label', the ScoreResult's primary_label is the label the user
    picked. For 'archive', it is deliberately the EMAIL'S OWN current
    primary_label, not the label picker's selection — SafetyPolicy's never-
    auto-archive guard for URGENT/FINANCE/PERSONAL keys off
    score.primary_label, and archiving doesn't change the email's category,
    so gating it on the picker's unrelated selection would let a sensitive
    email slip past that guard just because the dropdown happened to be set
    to something else.
    """
    email = resolve_email_for_action(db, gmail_id)
    if email is None:
        return False
    primary_label = chosen_label if action == "label" else current_primary_label
    score = synthetic_score(primary_label)
    return bool(executor.execute_action(email, action, score, confidence=1.0))
