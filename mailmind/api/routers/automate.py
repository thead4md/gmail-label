from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mailmind.api.auth import require_auth
from mailmind.api.deps import get_db
from mailmind.storage.queries import (
    build_digest,
    get_label_suggestions,
    get_ml_model_metadata,
    get_queue_stats,
    get_sender_profiles,
    set_label_suggestion_status,
    set_sender_label_rule,
    toggle_sender_auto_action,
    toggle_sender_auto_nudge,
)
from mailmind.taxonomy import BASE_SCORES as LABEL_BASE_SCORES

router = APIRouter(prefix="/api/automate", tags=["automate"], dependencies=[Depends(require_auth)])


@router.get("")
def automate(account: Optional[str] = None, days: int = 7) -> dict:
    db = get_db()
    since_ts = int(time.time()) - days * 86400

    newsletters = db.execute_sql(
        """
        SELECT DISTINCT e.sender, e.unsubscribe_url, COUNT(*) as email_count
        FROM emails e
        WHERE e.unsubscribe_url IS NOT NULL
          AND e.sender IS NOT NULL
          AND (e.account = ? OR ? IS NULL)
        GROUP BY e.sender, e.unsubscribe_url
        ORDER BY email_count DESC
        LIMIT 20
        """,
        (account, account),
    ).fetchall()

    return {
        "digest": build_digest(db, since_ts=since_ts, account=account),
        "sender_profiles": get_sender_profiles(db),
        "label_priorities": db.get_label_priorities(),
        "all_labels": list(LABEL_BASE_SCORES.keys()),
        "model_health": get_ml_model_metadata(db),
        "newsletters": [
            {"sender": r["sender"], "unsubscribe_url": r["unsubscribe_url"], "email_count": r["email_count"]}
            for r in newsletters
        ],
        "queue_stats": get_queue_stats(db, account=account),
        "label_suggestions": get_label_suggestions(db, status="pending"),
    }


class AutopilotBody(BaseModel):
    enabled: bool


@router.post("/senders/{email}/autopilot")
def set_autopilot(email: str, body: AutopilotBody) -> dict:
    toggle_sender_auto_action(get_db(), email, body.enabled)
    return {"ok": True}


@router.post("/senders/{email}/auto-nudge")
def set_auto_nudge(email: str, body: AutopilotBody) -> dict:
    """Grant or revoke earned autonomy for Loop Radar's follow-up nudges to
    this contact. Deliberately a separate endpoint/flag from /autopilot
    (label/star/archive autopilot) -- composing and sending new outbound
    content is a materially more consequential, harder-to-reverse action, so
    trusting a contact for one must never silently grant the other."""
    toggle_sender_auto_nudge(get_db(), email, body.enabled)
    return {"ok": True}


class LabelPriorityBody(BaseModel):
    label: str
    weight: int


@router.post("/label-priority")
def set_label_priority(body: LabelPriorityBody) -> dict:
    if not (-20 <= body.weight <= 30):
        raise HTTPException(status_code=422, detail="weight must be between -20 and 30")
    get_db().set_label_priority(body.label, body.weight)
    return {"ok": True}


class NlRuleBody(BaseModel):
    account: Optional[str] = None
    text: str


@router.post("/rules/nl")
def create_rule_from_nl(body: NlRuleBody) -> dict:
    from mailmind.config import MailMindConfig
    from mailmind.intelligence.nl_rules import parse_rule_nl
    from mailmind.llm.deepseek import DeepSeekClient

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")

    config = MailMindConfig.from_env()
    try:
        client = DeepSeekClient(config)
        result = parse_rule_nl(text, client)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error creating rule: {e}")

    if result.get("error") and not result.get("unsupported"):
        raise HTTPException(status_code=422, detail=result["error"])
    if result.get("unsupported"):
        raise HTTPException(status_code=422, detail=result.get("error") or "Unsupported rule description.")

    sender = result.get("sender_email")
    label = result.get("label")
    match_pattern = result.get("match_pattern")
    if not (sender and label):
        raise HTTPException(status_code=422, detail="Could not extract sender and label from your description.")

    set_sender_label_rule(get_db(), sender, label, account=body.account, match_pattern=match_pattern)
    return {"ok": True, "sender": sender, "label": label, "match_pattern": match_pattern}


@router.post("/label-suggestions/{suggestion_id}/{decision}")
def decide_label_suggestion(suggestion_id: int, decision: str) -> dict:
    if decision not in ("accepted", "dismissed"):
        raise HTTPException(status_code=422, detail="decision must be 'accepted' or 'dismissed'")
    set_label_suggestion_status(get_db(), suggestion_id, decision)
    return {"ok": True}
