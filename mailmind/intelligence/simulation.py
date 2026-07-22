"""MailMind — inbox simulation: "what breaks if I ignore this week?"
(client-strategy reframe §4.6).

A forward projection over the SAME data the Loops board already shows
(open "you owe" items + "waiting on" loops), answering a different
question: not "what's overdue right now" but "what will become a real
problem in the next 7 days if untouched." Turns generic anxiety ("I'm
behind") into a small, ranked, dated list.

Fully deterministic: no LLM, no network.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .deadline_parser import parse_deadline_string

# How many days out the simulation window looks.
WINDOW_DAYS = 7

# For a "you owe" item with no parseable deadline, project it "breaking"
# this many days after it first entered the queue -- when an unanswered,
# reply-needed message starts to feel genuinely stale. Always flagged
# is_estimated=True, never presented as a real deadline.
ESTIMATE_DAYS = 3


def _stakes(contact_rank: Optional[Dict[str, Any]]) -> float:
    """0.0-1.0 stakes multiplier from a relationship-graph rank entry (see
    intelligence/relationships.py). Unknown contact defaults to a neutral 0.5,
    matching that module's own neutral base score of 50/100."""
    if not contact_rank:
        return 0.5
    return max(0.0, min(1.0, (contact_rank.get("rank_score") or 50.0) / 100.0))


def simulate_week(
    you_owe_items: List[Dict[str, Any]],
    waiting_on_loops: List[Dict[str, Any]],
    rank_map: Dict[str, Dict[str, Any]],
    now_ts: int,
    window_days: int = WINDOW_DAYS,
    estimate_days: int = ESTIMATE_DAYS,
) -> List[Dict[str, Any]]:
    """Pure core: project which items will "break" within *window_days*.

    ``you_owe_items`` are enriched pending-queue rows (see
    queries.get_pending_queue_enriched); ``waiting_on_loops`` are loop rows
    (see queries.get_open_loops). Returns entries sorted soonest-first:
    {kind, ref_id, subject, contact, contact_email, breaks_at,
    breaks_in_days, is_estimated, stakes, vip}.
    """
    from .loops import split_addr

    window_end = now_ts + window_days * 86400
    results: List[Dict[str, Any]] = []

    for item in you_owe_items:
        reason = item.get("reason_json") or {}
        deadlines = reason.get("deadlines") or []
        breaks_at: Optional[int] = None
        is_estimated = False
        if deadlines:
            breaks_at = parse_deadline_string(deadlines[0], now_ts)
        if breaks_at is None:
            created = item.get("created_at")
            if created is not None:
                breaks_at = int(created) + estimate_days * 86400
                is_estimated = True
        if breaks_at is None or not (now_ts <= breaks_at <= window_end):
            continue

        contact_email, _ = split_addr(item.get("sender"))
        rank = rank_map.get(contact_email) if contact_email else None
        results.append({
            "kind": "you_owe",
            "ref_id": item.get("id"),
            "subject": item.get("subject"),
            "contact": item.get("display_name") or contact_email or item.get("sender"),
            "contact_email": contact_email,
            "breaks_at": breaks_at,
            "breaks_in_days": max(0, round((breaks_at - now_ts) / 86400.0, 1)),
            "is_estimated": is_estimated,
            "stakes": _stakes(rank),
            "vip": bool(rank and rank.get("vip")),
        })

    for loop in waiting_on_loops:
        breaks_at = loop.get("due_ts")
        if breaks_at is None or not (now_ts <= breaks_at <= window_end):
            continue
        contact_email = loop.get("contact_email")
        rank = rank_map.get(contact_email) if contact_email else None
        results.append({
            "kind": "waiting_on",
            "ref_id": loop.get("id"),
            "subject": loop.get("subject"),
            "contact": loop.get("contact_name") or contact_email,
            "contact_email": contact_email,
            "breaks_at": breaks_at,
            "breaks_in_days": max(0, round((breaks_at - now_ts) / 86400.0, 1)),
            "is_estimated": False,  # loops.due_ts is a real computed value, not a guess
            "stakes": _stakes(rank),
            "vip": bool(rank and rank.get("vip")),
        })

    results.sort(key=lambda r: (r["breaks_at"], -r["stakes"]))
    return results


def compute_weekly_simulation(
    db, account: Optional[str] = None, now_ts: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """DB-driven wrapper: fetch the same data /api/now already reads, plus
    the relationship graph, and delegate to simulate_week()."""
    import time as _time

    from ..processing.queue_manager import QueueManager, filter_now_items
    from ..storage.queries import get_pending_queue_enriched, get_open_loops
    from .relationships import get_contact_rank_map

    now = now_ts if now_ts is not None else int(_time.time())
    all_items = get_pending_queue_enriched(db, limit=200, account=account)
    you_owe = filter_now_items(all_items, queue_threshold=QueueManager.QUEUE_THRESHOLD)
    waiting_on = get_open_loops(db, account=account, side="waiting_on", limit=200)
    rank_map = get_contact_rank_map(db, account=account)

    return simulate_week(you_owe, waiting_on, rank_map, now)
