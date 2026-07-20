"""Reply/compose drafts — a deliberate three-step gate: Save Draft, Approve,
and Send are three SEPARATE endpoints/requests, never collapsible into fewer.
The real enforcement that a draft cannot be sent without a separate prior
approval lives server-side in feedback.handle_approve_and_send (it re-reads
the draft's status fresh from the database); this router's job is only to
expose each step as its own distinct HTTP call, never to bypass or duplicate
that enforcement. See that function's docstring for the full guarantee.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mailmind.api.auth import require_auth
from mailmind.api.deps import get_action_executor, get_db, get_llm_client
from mailmind.compose.composer import reply_subject
from mailmind.intelligence.feedback import handle_approve_and_send
from mailmind.storage.queries import create_draft, get_draft, update_draft_status

router = APIRouter(prefix="/api/drafts", tags=["drafts"], dependencies=[Depends(require_auth)])


def _extract_reply_to_addr(sender: str) -> str:
    sender = sender or ""
    if "<" in sender and ">" in sender:
        return sender.split("<", 1)[1].split(">", 1)[0].strip()
    return sender.strip()


class CreateDraftBody(BaseModel):
    account: Optional[str] = None
    in_reply_to_gmail_id: Optional[str] = None
    thread_id: Optional[str] = None
    to_addrs: str
    subject: str
    body_text: str = ""


@router.post("")
def create(body: CreateDraftBody) -> dict:
    draft_id = create_draft(
        get_db(), account=body.account, kind="reply",
        in_reply_to_gmail_id=body.in_reply_to_gmail_id, thread_id=body.thread_id,
        to_addrs=body.to_addrs, subject=body.subject, body_text=body.body_text,
        generated_by="human",
    )
    return {"id": draft_id}


@router.get("/{draft_id}")
def read(draft_id: int) -> dict:
    draft = get_draft(get_db(), draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return draft


@router.get("/reply-defaults/{gmail_id}")
def reply_defaults(gmail_id: str) -> dict:
    """Pre-fill values for a new reply — To/Subject derived from the original
    message, matching the Re:-prefix rule exactly (never doubled)."""
    row = get_db().get_email_by_gmail_id(gmail_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Email not found.")
    return {
        "to_addrs": _extract_reply_to_addr(row["sender"] or ""),
        "subject": reply_subject(row["subject"] or ""),
        "thread_id": row["thread_id"],
    }


@router.post("/{draft_id}/ai-draft")
def ai_draft(draft_id: int) -> dict:
    from mailmind.intelligence.draft_reply import draft_reply

    db = get_db()
    draft = get_draft(db, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    llm_client = get_llm_client()
    if llm_client is None:
        raise HTTPException(status_code=409, detail="AI drafting isn't configured for this deployment.")

    email_row = None
    if draft.get("in_reply_to_gmail_id"):
        email_row = db.get_email_by_gmail_id(draft["in_reply_to_gmail_id"])
    item = dict(email_row) if email_row is not None else {
        "subject": draft.get("subject"), "sender": draft.get("to_addrs"), "body_text": "", "snippet": "",
    }
    drafted = draft_reply(db, llm_client, item)
    if drafted is None:
        raise HTTPException(
            status_code=409,
            detail="Couldn't generate a draft right now (daily AI draft budget reached, or the model call failed).",
        )
    return {"body_text": drafted}


@router.post("/{draft_id}/approve")
def approve(draft_id: int) -> dict:
    ok = update_draft_status(get_db(), draft_id, "approved")
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return {"ok": True}


@router.post("/{draft_id}/discard")
def discard(draft_id: int) -> dict:
    ok = update_draft_status(get_db(), draft_id, "discarded")
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return {"ok": True}


class SendBody(BaseModel):
    account: Optional[str] = None


@router.post("/{draft_id}/send")
def send(draft_id: int, body: SendBody) -> dict:
    executor = get_action_executor(body.account)
    if executor is None:
        raise HTTPException(status_code=409, detail="No Gmail credentials found for this mailbox.")
    ok = handle_approve_and_send(get_db(), draft_id, executor)
    if not ok:
        raise HTTPException(status_code=409, detail="Send failed — see the draft's status to retry.")
    return {"ok": True}
