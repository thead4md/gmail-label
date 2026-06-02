from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Optional
import json

from ..storage.database import Database
from .sender_memory import get_sender_profile, get_similar_sender_history


@dataclass
class ReasonPayload:
    primary_label: str
    score: int
    score_breakdown: Dict[str, Any]
    rule_matches: List[str]
    ml_confidence: Optional[float]
    llm_confidence: Optional[float]
    trust_tier: str
    thread_summary: Optional[str]
    reply_needed: bool
    similar_past_actions: List[dict]
    action_items: List[str]
    deadlines: List[str]

    def to_json(self) -> str:
        return json.dumps({k: v for k, v in self.__dict__.items()}, default=str)


def build_reason_payload(db: Database, prediction, thread_context: Optional[dict] = None) -> ReasonPayload:
    # gather pieces
    trust_tier = 'neutral'
    similar = []
    if prediction is None:
        raise ValueError("prediction required")
    if prediction.rule_matches:
        rule_matches = prediction.rule_matches
    else:
        rule_matches = []
    if hasattr(prediction, 'ml_confidence'):
        ml_conf = prediction.ml_confidence
    else:
        ml_conf = None
    if hasattr(prediction, 'llm_confidence'):
        llm_conf = prediction.llm_confidence
    else:
        llm_conf = None

    if db and hasattr(prediction, 'email_gmail_id'):
        # try to get sender profile
        try:
            # resolve sender from email row
            row = db.execute_sql("SELECT sender FROM emails WHERE gmail_id = ?", (prediction.email_gmail_id,)).fetchone()
            sender = row['sender'] if row else None
            if sender:
                sp = get_sender_profile(db, sender)
                if sp:
                    trust_tier = sp.trust_tier
                    similar = get_similar_sender_history(db, sender, limit=5)
        except Exception:
            trust_tier = 'neutral'
            similar = []

    thread_summary = None
    reply_needed = False
    action_items: List[str] = []
    deadlines: List[str] = []
    if thread_context:
        thread_summary = thread_context.get('thread_summary')
        reply_needed = thread_context.get('reply_needed', False)
        action_items = thread_context.get('action_items') or []
        deadlines = thread_context.get('deadlines') or []

    payload = ReasonPayload(
        primary_label=prediction.primary_label or '',
        score=getattr(prediction, 'priority_score', 0),
        score_breakdown=json.loads(getattr(prediction, 'scoring_breakdown', '{}') or '{}'),
        rule_matches=rule_matches,
        ml_confidence=ml_conf,
        llm_confidence=llm_conf,
        trust_tier=trust_tier,
        thread_summary=thread_summary,
        reply_needed=reply_needed,
        similar_past_actions=similar,
        action_items=action_items,
        deadlines=deadlines,
    )
    return payload

