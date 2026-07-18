"""Canonical email-label taxonomy — the single source of truth for MailMind.

Every classifier tier (rules, ML, DeepSeek, OpenAI) and the priority scorer import their
label vocabulary and base scores from here, so the sets can no longer drift apart and no
tier can emit a label the scorer can't score. See CONTEXT.md Decisions Log.
"""
from __future__ import annotations

# Base priority scores (0-100). Every label any tier can emit MUST appear here.
BASE_SCORES: dict[str, int] = {
    "URGENT": 80,
    "WORK": 60,
    "FINANCE": 55,
    "CALENDAR": 55,        # was implicit 30 (no entry) — calendar invites are time-sensitive
    "PERSONAL": 50,
    "NOTIFICATION": 30,
    "DEFER": 20,
    "NEWSLETTER": 10,
    "MASS_EMAIL": 10,      # was implicit 30 (no entry) — bulk mail should rank low
    "SPAMCANDIDATE": 5,
    # OpenAI-tier-only labels (inactive unless LLM_ENABLED=true); kept at their current
    # effective value (30) so this change does not alter behavior for them.
    "ACTION_REQUIRED": 30,
    "MEETING": 30,
    "RECEIPT": 30,
    "SPAM": 30,
    "OTHER": 30,
}
DEFAULT_BASE_SCORE = 30
ALL_LABELS = frozenset(BASE_SCORES)

# Per-tier label vocabularies (subsets of ALL_LABELS).
ML_LABELS = [
    "URGENT", "WORK", "FINANCE", "PERSONAL", "CALENDAR",
    "NOTIFICATION", "NEWSLETTER", "MASS_EMAIL", "SPAMCANDIDATE", "DEFER",
]
DEEPSEEK_LABELS = frozenset({
    "NOTIFICATION", "NEWSLETTER", "MASS_EMAIL", "PERSONAL", "FINANCE", "CALENDAR",
    "WORK", "URGENT",
})
OPENAI_LABELS = frozenset({
    "NEWSLETTER", "NOTIFICATION", "MASS_EMAIL", "PERSONAL", "FINANCE",
    "ACTION_REQUIRED", "MEETING", "RECEIPT", "SPAM", "OTHER",
})
REVIEW_LABELS = frozenset({"PERSONAL", "ACTION_REQUIRED", "FINANCE", "MEETING"})

def base_score(label: str | None) -> int:
    if not label:
        return DEFAULT_BASE_SCORE
    return BASE_SCORES.get(label.strip().upper(), DEFAULT_BASE_SCORE)

def is_known(label: str | None) -> bool:
    return bool(label) and label.strip().upper() in ALL_LABELS
