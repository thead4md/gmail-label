"""Actions on action_queue rows — shared by the NOW and REVIEW pages, which
both operate on the same pending items. Every handler here is a direct pass-
through to mailmind.intelligence.feedback; this module adds no business logic
of its own beyond translating HTTP <-> those calls."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mailmind.api.auth import require_auth
from mailmind.api.deps import get_action_executor, get_db
from mailmind.intelligence.feedback import (
    handle_approve,
    handle_correction,
    handle_label_email,
    handle_reject,
)

router = APIRouter(prefix="/api/queue", tags=["queue"], dependencies=[Depends(require_auth)])


class ApproveBody(BaseModel):
    account: Optional[str] = None
    corrected_label: Optional[str] = None


class RejectBody(BaseModel):
    account: Optional[str] = None


class LabelBody(BaseModel):
    account: Optional[str] = None
    label: str
    scope: str = "email"
    match_pattern: Optional[str] = None


class CorrectBody(BaseModel):
    account: Optional[str] = None
    label: str


@router.post("/{queue_id}/approve")
def approve(queue_id: int, body: ApproveBody) -> dict:
    db = get_db()
    if body.corrected_label:
        handle_correction(db, queue_id, corrected_label=body.corrected_label)
    ok = handle_approve(db, queue_id, executor=get_action_executor(body.account))
    if not ok:
        raise HTTPException(status_code=404, detail="Already processed or no longer exists.")
    return {"ok": True}


@router.post("/{queue_id}/reject")
def reject(queue_id: int, body: RejectBody) -> dict:
    ok = handle_reject(get_db(), queue_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Already processed or no longer exists.")
    return {"ok": True}


@router.post("/{queue_id}/label")
def label(queue_id: int, body: LabelBody) -> dict:
    """Create a label rule from user feedback (REVIEW's 'Edit label' flow).
    scope='email' is a one-off correction; 'thread'/'sender' also create a
    standing rule and reapply immediately if credentials are available."""
    if body.scope not in ("email", "thread", "sender"):
        raise HTTPException(status_code=422, detail="scope must be 'email', 'thread', or 'sender'")
    db = get_db()
    if body.scope == "email":
        ok = handle_correction(db, queue_id, corrected_label=body.label)
    else:
        ok = handle_label_email(
            db, queue_id, body.label, body.scope,
            executor=get_action_executor(body.account),
            account=body.account, match_pattern=body.match_pattern,
        )
    if not ok:
        raise HTTPException(status_code=404, detail="Item no longer exists.")
    return {"ok": True}


@router.post("/{queue_id}/correct")
def correct(queue_id: int, body: CorrectBody) -> dict:
    """HISTORY's 'Correct label' on an already-terminal (executed) item — no
    later Approve step exists to pick this up, so the executor is passed
    directly so the fix reaches Gmail immediately, mirroring app.py's
    render_history_tab exactly."""
    ok = handle_correction(
        get_db(), queue_id, corrected_label=body.label,
        executor=get_action_executor(body.account),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Item no longer found in queue.")
    return {"ok": True}
