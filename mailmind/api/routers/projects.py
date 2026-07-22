from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mailmind.api.auth import require_auth
from mailmind.api.deps import get_db
from mailmind.storage.queries import get_project, get_projects, close_project

router = APIRouter(prefix="/api/projects", tags=["projects"], dependencies=[Depends(require_auth)])


@router.get("")
def list_projects(account: Optional[str] = None, status: Optional[str] = "active") -> dict:
    db = get_db()
    return {"items": get_projects(db, account=account, status=status or None)}


@router.get("/{project_id}")
def read_project(project_id: int) -> dict:
    db = get_db()
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


class PromoteThreadBody(BaseModel):
    account: Optional[str] = None


@router.post("/from-thread/{thread_id}")
def promote_thread(thread_id: str, body: PromoteThreadBody) -> dict:
    """Promote a thread into a durable project (§4.5). Idempotent -- calling
    this again on the same thread refreshes the existing project rather than
    creating a duplicate."""
    from mailmind.intelligence.projects import promote_thread_to_project

    db = get_db()
    try:
        project_id = promote_thread_to_project(db, thread_id, account=body.account)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return get_project(db, project_id)


@router.post("/{project_id}/close")
def close(project_id: int) -> dict:
    db = get_db()
    if get_project(db, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    close_project(db, project_id)
    return {"ok": True}
