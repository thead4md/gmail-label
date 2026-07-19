"""AI-drafted reply generation for MailMind's compose UI (Phase 4).

Human-triggered only — this module is called exactly once, when a user clicks
"Draft with AI" in the compose UI. It never runs automatically as part of the
classification pipeline, and its cost is hard-capped against a daily budget
checked via the durable ``llm_usage`` table (the same table the INSIGHTS tab's
"LLM spend" section already reads) before ever calling the LLM.

Built on top of the existing provider-agnostic ``chat_complete()`` helper
(``mailmind.llm.chat``) rather than talking to DeepSeek/OpenAI clients
directly, so this works with whichever LLM provider is configured — exactly
like the daily-brief, NL-rule-parsing, and label-discovery features already
do.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Optional

from ..storage.database import Database

LOG = logging.getLogger(__name__)

# Matches DeepSeekClient.classify_email's existing body-truncation convention
# (mailmind/llm/deepseek.py) so this feature's prompts stay in the same size
# ballpark as the rest of the LLM call sites in this codebase.
_MAX_BODY_CHARS = 500
_MAX_SUBJECT_CHARS = 200
_MAX_THREAD_SUMMARY_CHARS = 300

# Cheap heuristic, not real language detection — Hungarian diacritics/words
# appear routinely in this real mailbox's content (a scouting organization's
# correspondence), and asking the model to reply in the wrong language is a
# worse failure mode than a slightly-imprecise heuristic.
_HU_HINTS = (
    "á", "é", "í", "ó", "ö", "ő", "ú", "ü", "ű",
    "kedves", "üdvözlettel", "szia", "köszönöm", "tisztelt",
)


def _looks_hungarian(*texts: Optional[str]) -> bool:
    combined = " ".join(t for t in texts if t).lower()
    return any(hint in combined for hint in _HU_HINTS)


def _day_start_ts() -> int:
    """Local-midnight epoch for today. Mirrors dashboard/app.py's
    ``_day_start_ts(days_ago=0)`` so "today's" spend is computed the same way
    everywhere this codebase reports daily LLM cost."""
    d = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp())


def draft_reply(
    db: Database,
    llm_client: Any,
    email: dict,
    thread_summary: Optional[str] = None,
    daily_cost_cap_usd: float = 0.50,
) -> Optional[str]:
    """Generate an AI-drafted reply to ``email``, or None if unavailable.

    ``email`` is a plain dict (e.g. a row from ``get_all_emails``/
    ``get_thread_emails``, or a resurrected DB row) expected to carry at least
    ``subject``/``sender``/``body_text``/``snippet``; ``action_items`` and
    ``deadlines`` (from ``ThreadContext``, if the caller has them handy) are
    used opportunistically via ``.get()`` — never required.

    Checks today's LLM spend against ``daily_cost_cap_usd`` BEFORE calling the
    LLM — a hard cap, not a warning: once today's tracked spend meets or
    exceeds the cap, this returns None without making a call. Never raises;
    any failure (missing client, LLM error, cap exceeded) returns None so the
    caller (the compose UI) can show a friendly message and let the user type
    their own reply instead.
    """
    if llm_client is None:
        return None

    from ..storage.queries import analytics_llm_cost, record_llm_usage

    try:
        spent_today = analytics_llm_cost(db, since_ts=_day_start_ts()).get("cost_usd", 0.0)
    except Exception:
        LOG.debug("draft_reply: analytics_llm_cost failed, refusing to draft", exc_info=True)
        return None

    if spent_today >= daily_cost_cap_usd:
        LOG.info(
            "draft_reply: daily cost cap reached ($%.4f >= $%.2f) — refusing to draft",
            spent_today, daily_cost_cap_usd,
        )
        return None

    subject = (email.get("subject") or "")[:_MAX_SUBJECT_CHARS]
    sender = email.get("sender") or "unknown sender"
    body = (email.get("body_text") or email.get("snippet") or "")[:_MAX_BODY_CHARS]
    action_items = email.get("action_items") or []
    deadlines = email.get("deadlines") or []
    summary = (thread_summary or "")[:_MAX_THREAD_SUMMARY_CHARS]

    hungarian = _looks_hungarian(subject, body, summary)
    language_instruction = (
        "Write the reply in Hungarian." if hungarian else "Write the reply in English."
    )

    system = (
        "You draft short, professional-but-friendly email replies for a busy person. "
        "Reply with ONLY the body text of the reply — no subject line, no greeting "
        "boilerplate beyond a natural salutation, no explanations about what you did. "
        + language_instruction
    )

    user_parts = [f"Original email from {sender}:", f"Subject: {subject}", "", body]
    if summary:
        user_parts += ["", f"Thread context: {summary}"]
    if action_items:
        user_parts += ["", "Open action items in this thread: " + "; ".join(action_items[:5])]
    if deadlines:
        user_parts += ["", "Mentioned deadlines: " + "; ".join(deadlines[:5])]
    user_parts += ["", "Draft a concise reply."]
    user = "\n".join(user_parts)

    try:
        from ..llm.chat import chat_complete

        t0 = time.monotonic()
        content, resp, model = chat_complete(
            llm_client, system, user,
            temperature=0.4, max_tokens=300, return_usage=True,
        )
        elapsed_s = time.monotonic() - t0
    except Exception:
        LOG.warning("draft_reply: LLM call failed", exc_info=True)
        return None

    try:
        from ..ml.llm_classifier import log_llm_usage, drain_pending_usage

        log_llm_usage(model, resp, elapsed_s, kind="draft_reply")
        record_llm_usage(db, drain_pending_usage())
    except Exception:
        LOG.debug("draft_reply: usage recording failed (non-fatal)", exc_info=True)

    content = (content or "").strip()
    return content or None
