from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mailmind.actions.bulk import run_bulk_action
from mailmind.api.auth import require_auth
from mailmind.api.deps import get_action_executor, get_db
from mailmind.taxonomy import ALL_LABELS
from mailmind.storage.queries import get_all_emails, get_gmail_labels, get_thread_emails

router = APIRouter(prefix="/api/inbox", tags=["inbox"], dependencies=[Depends(require_auth)])


@router.get("")
def list_inbox(account: Optional[str] = None, offset: int = 0, limit: int = 50) -> dict:
    db = get_db()
    items = get_all_emails(db, account=account, limit=limit, offset=offset)
    return {"items": items, "offset": offset, "limit": limit}


@router.get("/threads/{thread_id}")
def thread(thread_id: str, account: Optional[str] = None) -> list:
    return get_thread_emails(get_db(), thread_id, account=account)


class BulkActionBody(BaseModel):
    account: Optional[str] = None
    ids: List[str]
    action: str  # "label" | "archive"
    label: Optional[str] = None


@router.post("/bulk")
def bulk_action(body: BulkActionBody) -> dict:
    if body.action not in ("label", "archive"):
        raise HTTPException(status_code=422, detail="action must be 'label' or 'archive'")
    if body.action == "label" and not body.label:
        raise HTTPException(status_code=422, detail="label is required for action='label'")
    db = get_db()
    executor = get_action_executor(body.account)
    if executor is None:
        raise HTTPException(status_code=409, detail="No Gmail credentials found for this mailbox.")

    # Need each item's current primary_label for the 'archive' case (see
    # run_bulk_action's docstring for why archive never uses the label
    # picker's value) — look up exactly the selected ids' most recent
    # prediction, not a guessed page of "recent" emails that could miss ids
    # selected from further back in a long, paginated list.
    placeholders = ",".join("?" for _ in body.ids)
    rows = db.execute_sql(
        f"""
        SELECT e.gmail_id AS gmail_id,
               (SELECT p.primary_label FROM predictions p
                WHERE p.email_gmail_id = e.gmail_id
                ORDER BY p.id DESC LIMIT 1) AS primary_label
        FROM emails e WHERE e.gmail_id IN ({placeholders})
        """,
        tuple(body.ids),
    ).fetchall() if body.ids else []
    by_id = {r["gmail_id"]: r["primary_label"] for r in rows}

    success = 0
    failed = 0
    for gmail_id in body.ids:
        current_label = by_id.get(gmail_id)
        ok = run_bulk_action(db, executor, gmail_id, body.action, current_label, body.label)
        if ok:
            success += 1
        else:
            failed += 1
    return {"success": success, "failed": failed}


@router.get("/labels")
def labels(account: Optional[str] = None) -> list:
    return get_gmail_labels(get_db(), account=account) or list(ALL_LABELS)
