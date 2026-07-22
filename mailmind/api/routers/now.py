from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends

from mailmind.api.auth import require_auth
from mailmind.api.deps import get_db, get_llm_client
from mailmind.intelligence.loops import split_addr
from mailmind.intelligence.relationships import get_contact_rank_map
from mailmind.processing.queue_manager import QueueManager, filter_now_items
from mailmind.storage.queries import (
    build_digest,
    get_calendar_hold_for_email,
    get_executed_queue_enriched,
    get_gmail_labels,
    get_open_loops,
    get_pending_queue_enriched,
)
from mailmind.taxonomy import ALL_LABELS

router = APIRouter(prefix="/api/now", tags=["now"], dependencies=[Depends(require_auth)])


def _day_start_ts(days_ago: int = 0) -> int:
    d = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return int((d - timedelta(days=days_ago)).timestamp())


def _kpis(account: Optional[str]) -> list[dict]:
    db = get_db()
    today = build_digest(db, since_ts=_day_start_ts(0), account=account) or {}
    yesterday_plus_today = build_digest(db, since_ts=_day_start_ts(1), account=account) or {}

    def _delta(key: str) -> int:
        yesterday_only = int(yesterday_plus_today.get(key, 0)) - int(today.get(key, 0))
        return int(today.get(key, 0)) - yesterday_only

    return [
        {"icon": "📨", "label": "Triaged today", "value": int(today.get("classified", 0)), "delta": _delta("classified")},
        {"icon": "🤖", "label": "Auto-labeled", "value": int(today.get("executed", 0)), "delta": _delta("executed")},
        {"icon": "📥", "label": "Awaiting review", "value": int(today.get("queued", 0)), "delta": None},
        {"icon": "💬", "label": "Reply needed", "value": int(today.get("pending_reply_needed", 0)), "delta": None},
    ]


@router.get("")
def get_now(account: Optional[str] = None) -> dict:
    """The reframed NOW payload: three lanes of open loops.

    - ``you_owe``    — pending queue items needing a reply/decision from the
      user (derived on read from the action queue via ``filter_now_items``).
      Also annotated with ``calendar_hold`` (the most recent non-discarded
      calendar hold proposed for this email, if any -- §4.4) so the UI can
      surface a one-click Approve/Discard without a second request.
    - ``waiting_on`` — durable loops where someone owes the user a reply
      (populated by the watch-loop detector; see intelligence/loops.py). Each is
      annotated with ``waiting_days``, a ``slipping`` flag (past its due_ts),
      and a ``vip``/``rank_score`` from the relationship graph
      (intelligence/relationships.py, §4.3). Sort order is untouched by VIP
      status (stalest-first, unchanged from V1) -- ``vip`` is surfaced as a
      badge, not a re-ranking, to avoid changing established triage order.
    - ``handled``    — recently executed/auto-labeled items (what MailMind did),
      shown collapsed.

    ``items`` is kept as an alias of ``you_owe`` for backward compatibility.
    """
    db = get_db()
    all_items = get_pending_queue_enriched(db, limit=200, account=account)
    you_owe = filter_now_items(all_items, queue_threshold=QueueManager.QUEUE_THRESHOLD)

    now = int(datetime.now().timestamp())
    waiting_on = get_open_loops(db, account=account, side="waiting_on", limit=100)

    try:
        rank_map = get_contact_rank_map(db, account=account)
    except Exception:
        rank_map = {}

    slipping = 0
    for lp in waiting_on:
        last = lp.get("last_activity_ts") or lp.get("last_sent_ts")
        lp["waiting_days"] = int((now - last) / 86400) if last else None
        due = lp.get("due_ts")
        lp["slipping"] = bool(due and due <= now)
        if lp["slipping"]:
            slipping += 1
        rank = rank_map.get(lp.get("contact_email"))
        lp["vip"] = bool(rank and rank["vip"])
        lp["rank_score"] = rank["rank_score"] if rank else None

    for item in you_owe:
        contact_email, _ = split_addr(item.get("sender"))
        rank = rank_map.get(contact_email)
        item["vip"] = bool(rank and rank["vip"])
        item["rank_score"] = rank["rank_score"] if rank else None
        try:
            item["calendar_hold"] = get_calendar_hold_for_email(db, item["email_gmail_id"])
        except Exception:
            item["calendar_hold"] = None

    handled = get_executed_queue_enriched(db, limit=25, account=account)
    gmail_labels = get_gmail_labels(db, account=account) or list(ALL_LABELS)
    return {
        "kpis": _kpis(account),
        "items": you_owe,  # backward-compat alias
        "you_owe": you_owe,
        "waiting_on": waiting_on,
        "handled": handled,
        "counts": {
            "you_owe": len(you_owe),
            "waiting_on": len(waiting_on),
            "slipping": slipping,
        },
        "gmail_labels": gmail_labels,
    }


@router.get("/simulation")
def get_weekly_simulation(account: Optional[str] = None) -> dict:
    """Inbox simulation (§4.6): which open items will "break" in the next 7
    days if ignored. Fully deterministic (no LLM); a separate endpoint so a
    failure here never affects the core NOW payload."""
    from mailmind.intelligence.simulation import compute_weekly_simulation

    db = get_db()
    try:
        items = compute_weekly_simulation(db, account=account)
    except Exception:
        items = []
    return {"items": items}


@router.get("/brief")
def get_daily_brief(account: Optional[str] = None) -> dict:
    """Separate, slower endpoint (may call the LLM) so the frontend can render
    the feed immediately and stream the brief in once ready, instead of
    blocking the whole NOW payload on it."""
    from mailmind.intelligence.brief import build_daily_brief

    db = get_db()
    brief = build_daily_brief(db, account=account, llm_client=get_llm_client())
    return {"brief": brief}
