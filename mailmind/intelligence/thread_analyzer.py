from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import re
import logging

LOG = logging.getLogger(__name__)


@dataclass
class ThreadContext:
    is_thread: bool
    thread_length: int
    reply_needed: bool
    open_question_detected: bool
    waiting_on_other_party: bool
    thread_summary: Optional[str] = None


class ThreadAnalyzer:
    """Heuristic-first thread analyzer with optional LLM summarization.

    Rules are intentionally conservative and deterministic.
    """

    REPLY_PHRASES = [
        r"please confirm",
        r"can you",
        r"let me know",
        r"could you",
        r"please",
        r"will you",
    ]

    WAITING_PHRASES = [
        r"we'll update",
        r"i'll get back",
        r"i will get back",
        r"we will get back",
        r"follow up",
    ]

    QUESTION_RE = re.compile(r"\?")

    @staticmethod
    def analyze(email, db=None) -> ThreadContext:
        # Basic heuristics
        body = (email.body_text or "").lower()
        # is_thread: true if thread_id present or 're:' in subject
        is_thread = bool(email.thread_id) or (email.subject and email.subject.lower().startswith("re:"))
        # thread_length: count of 'On ' markers or 're:' occurrences as a heuristic
        thread_length = (body.count('\n>') + body.count('re:') + 1) if is_thread else 1

        # reply_needed: detect phrases or question marks
        reply_needed = False
        for pat in ThreadAnalyzer.REPLY_PHRASES:
            if re.search(pat, body):
                reply_needed = True
                break
        if not reply_needed and ThreadAnalyzer.QUESTION_RE.search(body):
            reply_needed = True

        # open_question_detected: presence of question marks
        open_question_detected = bool(ThreadAnalyzer.QUESTION_RE.search(body))

        # waiting_on_other_party
        waiting_on_other_party = any(re.search(p, body) for p in ThreadAnalyzer.WAITING_PHRASES)

        # thread_summary: optional lightweight first-line summary (first 200 chars)
        summary = None
        try:
            first_lines = " ".join(line.strip() for line in body.splitlines() if line.strip())
            if first_lines:
                summary = first_lines[:200]
        except Exception as e:
            LOG.debug("Failed to build thread summary: %s", e)
            summary = None

        return ThreadContext(
            is_thread=is_thread,
            thread_length=max(1, int(thread_length)),
            reply_needed=reply_needed,
            open_question_detected=open_question_detected,
            waiting_on_other_party=waiting_on_other_party,
            thread_summary=summary,
        )

