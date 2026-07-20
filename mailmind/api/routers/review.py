from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from mailmind.api.auth import require_auth
from mailmind.api.deps import get_db
from mailmind.intelligence.feedback import handle_block_sender, handle_know_sender, handle_mute_sender
from mailmind.storage.queries import get_new_senders, get_pending_queue_enriched, get_recent_predictions_with_emails

router = APIRouter(prefix="/api/review", tags=["review"], dependencies=[Depends(require_auth)])


@router.get("/new-senders")
def new_senders(account: Optional[str] = None) -> list:
    return get_new_senders(get_db(), account=account)


@router.post("/new-senders/{sender}/know")
def know_sender(sender: str) -> dict:
    handle_know_sender(get_db(), sender)
    return {"ok": True}


@router.post("/new-senders/{sender}/mute")
def mute_sender(sender: str) -> dict:
    handle_mute_sender(get_db(), sender)
    return {"ok": True}


@router.post("/new-senders/{sender}/block")
def block_sender(sender: str) -> dict:
    handle_block_sender(get_db(), sender)
    return {"ok": True}


@router.get("/predictions")
def recent_predictions(account: Optional[str] = None) -> list:
    return get_recent_predictions_with_emails(get_db(), limit=200, account=account)


@router.get("/pending")
def pending(account: Optional[str] = None, offset: int = 0, limit: int = 25) -> dict:
    items = get_pending_queue_enriched(get_db(), limit=None, account=account)
    return {"total": len(items), "items": items[offset:offset + limit]}
