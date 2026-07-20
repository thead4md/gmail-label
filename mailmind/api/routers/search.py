from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from mailmind.api.auth import require_auth
from mailmind.api.deps import get_db
from mailmind.storage.queries import search_emails

router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Depends(require_auth)])


@router.get("")
def search(q: str, account: Optional[str] = None, offset: int = 0, limit: int = 50) -> dict:
    if not q.strip():
        return {"items": [], "total": 0}
    items = search_emails(get_db(), q.strip(), account=account, limit=limit, offset=offset)
    return {"items": items, "query": q.strip()}
