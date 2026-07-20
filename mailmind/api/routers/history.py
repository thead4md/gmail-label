from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends

from mailmind.api.auth import require_auth
from mailmind.api.deps import get_db
from mailmind.storage.queries import get_executed_queue_enriched, get_recent_corrections

router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[Depends(require_auth)])


@router.get("/executed")
def executed(account: Optional[str] = None, days: int = 7, offset: int = 0, limit: int = 25) -> dict:
    cutoff_ts = int(time.time()) - days * 86400
    all_items = get_executed_queue_enriched(get_db(), limit=200, account=account)
    items = [
        it for it in all_items
        if (it.get("executed_at") or it.get("reviewed_at") or it.get("created_at") or 0) >= cutoff_ts
    ]
    return {"total": len(items), "items": items[offset:offset + limit]}


@router.get("/corrections")
def corrections(limit: int = 50) -> list:
    return get_recent_corrections(get_db(), limit=limit)
