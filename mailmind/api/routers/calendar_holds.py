from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mailmind.api.auth import require_auth
from mailmind.api.deps import get_calendar_client, get_db
from mailmind.storage.queries import (
    get_calendar_hold,
    get_pending_calendar_holds,
    update_calendar_hold_status,
)

router = APIRouter(prefix="/api/calendar-holds", tags=["calendar-holds"], dependencies=[Depends(require_auth)])


class ApproveCalendarHoldBody(BaseModel):
    account: Optional[str] = None


@router.get("")
def list_calendar_holds(account: Optional[str] = None) -> dict:
    db = get_db()
    return {"items": get_pending_calendar_holds(db, account=account)}


@router.post("/{hold_id}/approve")
def approve_calendar_hold(hold_id: int, body: ApproveCalendarHoldBody) -> dict:
    """The single explicit human action for a calendar hold: creates the
    real Google Calendar event immediately. Deliberately not a 3-step gate
    like drafts -- see actions/calendar.py's module docstring for why a
    calendar hold's risk profile doesn't call for the extra step irreversible
    email-sending does."""
    db = get_db()
    hold = get_calendar_hold(db, hold_id)
    if hold is None:
        raise HTTPException(status_code=404, detail="Calendar hold not found")
    if hold["status"] != "proposed":
        raise HTTPException(status_code=409, detail=f"Hold is '{hold['status']}', not 'proposed'")

    client = get_calendar_client(body.account)
    if client is None:
        raise HTTPException(status_code=409, detail="No connected calendar for this mailbox")

    event_id = client.create_event(hold["summary"], hold["start_ts"], hold["end_ts"])
    if event_id:
        update_calendar_hold_status(db, hold_id, "created", gcal_event_id=event_id, created_by="human")
        return get_calendar_hold(db, hold_id)

    update_calendar_hold_status(db, hold_id, "create_failed")
    raise HTTPException(status_code=502, detail="Failed to create the calendar event")


@router.post("/{hold_id}/discard")
def discard_calendar_hold(hold_id: int) -> dict:
    db = get_db()
    if get_calendar_hold(db, hold_id) is None:
        raise HTTPException(status_code=404, detail="Calendar hold not found")
    update_calendar_hold_status(db, hold_id, "discarded")
    return {"ok": True}
