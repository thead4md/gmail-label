from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from mailmind.api.auth import require_auth
from mailmind.api.deps import get_db
from mailmind.taxonomy import ALL_LABELS
from mailmind.storage.queries import get_all_emails, get_gmail_labels

router = APIRouter(prefix="/api/folders", tags=["folders"], dependencies=[Depends(require_auth)])


@router.get("")
def list_folders(account: Optional[str] = None) -> list:
    return get_gmail_labels(get_db(), account=account) or list(ALL_LABELS)


@router.get("/{label}")
def folder_emails(label: str, account: Optional[str] = None, offset: int = 0, limit: int = 50) -> dict:
    items = get_all_emails(get_db(), account=account, folder=label, limit=limit, offset=offset)
    return {"items": items, "label": label}
