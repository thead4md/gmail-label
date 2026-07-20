from __future__ import annotations

from fastapi import APIRouter, Depends

from mailmind.api.auth import require_auth
from mailmind.api.deps import get_accounts, get_db
from mailmind.main import HEARTBEAT_KEY, get_heartbeat_status

router = APIRouter(prefix="/api/meta", tags=["meta"], dependencies=[Depends(require_auth)])


@router.get("")
def get_meta() -> dict:
    """Bootstrap payload the frontend fetches once on load: accounts + watcher
    heartbeat, matching the sidebar's mailbox switcher + heartbeat indicator."""
    db = get_db()
    raw = db.get_state(HEARTBEAT_KEY)
    hb = get_heartbeat_status(int(raw) if raw else None)
    return {"accounts": get_accounts(), "heartbeat": hb}
