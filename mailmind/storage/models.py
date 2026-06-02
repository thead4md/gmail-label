"""Data models for MailMind storage layer.

These are plain dataclasses used by the storage/database abstraction.
They intentionally keep content fields optional and small to respect privacy-by-default.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, List
from datetime import datetime, timezone
from pathlib import Path


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


@dataclass
class Email:
    # Primary fields
    gmail_id: str
    thread_id: Optional[str] = None
    sender: Optional[str] = None
    recipients: Optional[List[str]] = field(default_factory=list)
    subject: Optional[str] = None
    snippet: Optional[str] = None
    # Prefer storing a small safe/plaintext excerpt; full bodies should be optional and
    # guarded by privacy settings in higher layers.
    body_text: Optional[str] = None
    date_ts: Optional[int] = None
    labels: Optional[List[str]] = field(default_factory=list)
    parsed: bool = False
    # Which mailbox this email belongs to (multi-account). None for legacy rows.
    account: Optional[str] = None

    def to_db_tuple(self) -> tuple:
        # Convert lists to JSON-like strings (comma-separated). Keep this simple for now.
        recipients_s = ",".join(self.recipients) if self.recipients else None
        labels_s = ",".join(self.labels) if self.labels else None
        return (
            self.gmail_id,
            self.thread_id,
            self.sender,
            recipients_s,
            self.subject,
            self.snippet,
            self.body_text,
            self.date_ts,
            labels_s,
            int(bool(self.parsed)),
            self.account,
        )


@dataclass
class Prediction:
    """Prediction model: rules/ML/LLM output for an email.
    
    This model captures the full pipeline decision for an email, including:
    - deterministic rules matching
    - priority scoring
    - optional ML/LLM confidence values
    - persisted audit trail of the decision
    """
    email_gmail_id: str
    model: str  # "rules" for MVP, "ml" for Phase 4+, etc.
    labels: List[str]
    priority_score: int  # Main scoring value (0-100)
    primary_label: Optional[str] = None  # Top-priority label determined from rules/scoring
    confidence: float = 0.85  # Confidence in this prediction (deterministic: 0.85 for rules, varies for ML)
    
    # Extended fields for Pass 3+ pipeline stability
    pipeline_used: str = "rules"  # "rules", "ml", or "hybrid" (for future multimodel)
    action_suggested: Optional[str] = None  # "label", "star", "archive", etc. (if applicable)
    rule_matches: Optional[List[str]] = field(default_factory=list)  # list of rule names that matched
    scoring_breakdown: Optional[str] = None  # JSON-serializable dict or text breakdown for debugging
    
    # ML/LLM confidence (nullable for rules-only MVP)
    ml_confidence: Optional[float] = None  # ML model confidence (Phase 4+)
    llm_confidence: Optional[float] = None  # LLM confidence (Phase 5+)
    
    # LLM classification fields (Phase 7+: third-tier fallback)
    llm_label: Optional[str] = None  # LLM-classified label
    llm_rationale: Optional[str] = None  # One-sentence rationale from LLM
    llm_action_hint: Optional[str] = None  # Suggested action from LLM (nullable)
    llm_needs_review: bool = False  # True if human review is needed
    classifier_source: str = "rules"  # "rules" | "ml" | "llm" | "fallback"
    llm_called_at: Optional[str] = None  # ISO-8601 timestamp when LLM was called

    # Thread context (serialized JSON)
    thread_context_json: Optional[str] = None

    # Legacy field: kept for backward compatibility
    score: Optional[int] = None  # Deprecated in favor of priority_score

    created_at: int = field(default_factory=now_ts)

    # Row id, populated by save_prediction() after persistence.
    id: Optional[int] = None

    # Which mailbox this prediction belongs to (multi-account). None = legacy.
    account: Optional[str] = None

    # Communication channel detected by intelligence/channels.py. None = not yet detected.
    channel: Optional[str] = None


@dataclass
class ActionApplied:
    email_gmail_id: str
    action: str
    params: Optional[dict] = field(default_factory=dict)
    dry_run: bool = True
    succeeded: Optional[bool] = None
    created_at: int = field(default_factory=now_ts)
    details: Optional[str] = None


@dataclass
class Feedback:
    email_gmail_id: str
    user_feedback: str
    created_at: int = field(default_factory=now_ts)


@dataclass
class SenderReputation:
    sender: str
    score: float
    last_seen: int = field(default_factory=now_ts)


@dataclass
class SystemState:
    key: str
    value: str
    updated_at: int = field(default_factory=now_ts)


@dataclass
class QueueItem:
    email_gmail_id: str
    action: str
    action_fingerprint: str
    id: Optional[int] = None
    prediction_id: Optional[int] = None
    params: dict = field(default_factory=dict)
    status: str = 'pending'
    confidence: float = 0.0
    priority_score: int = 0
    reason_json: dict = field(default_factory=dict)
    created_at: int = field(default_factory=now_ts)
    updated_at: int = field(default_factory=now_ts)
    # Which mailbox this queue item belongs to (multi-account). None = legacy.
    account: Optional[str] = None
    reviewed_at: Optional[int] = None
    executed_at: Optional[int] = None
