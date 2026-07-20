from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends

from mailmind.api.auth import require_auth
from mailmind.api.deps import get_db
from mailmind.processing.queue_manager import QueueManager, filter_now_items
from mailmind.storage.queries import build_digest, get_gmail_labels, get_pending_queue_enriched
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
    db = get_db()
    all_items = get_pending_queue_enriched(db, limit=200, account=account)
    now_items = filter_now_items(all_items, queue_threshold=QueueManager.QUEUE_THRESHOLD)
    gmail_labels = get_gmail_labels(db, account=account) or list(ALL_LABELS)
    return {
        "kpis": _kpis(account),
        "items": now_items,
        "gmail_labels": gmail_labels,
    }


@router.get("/brief")
def get_daily_brief(account: Optional[str] = None) -> dict:
    """Separate, slower endpoint (may call the LLM) so the frontend can render
    the feed immediately and stream the brief in once ready, instead of
    blocking the whole NOW payload on it."""
    from mailmind.config import MailMindConfig
    from mailmind.intelligence.brief import build_daily_brief
    from mailmind.llm.deepseek import DeepSeekClient

    db = get_db()
    config = MailMindConfig.from_env()
    llm_client = None
    if config.llm_enabled and config.deepseek_api_key:
        try:
            llm_client = DeepSeekClient(config)
        except Exception:
            llm_client = None
    brief = build_daily_brief(db, account=account, llm_client=llm_client)
    return {"brief": brief}
