"""Third-tier LLM classifier for MailMind using OpenAI-compatible API.

Called only when rules engine and local ML model are not confident enough.
Returns strictly structured JSON every time. Never raises exceptions.

This module provides both a legacy LLMClassifier interface and an OpenAIAdapter
that conforms to the unified LLMClassifier Protocol.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from ..taxonomy import OPENAI_LABELS as VALID_LABELS, REVIEW_LABELS
from ..storage.models import Email
from ..llm.base import LLMResult

LOG = logging.getLogger(__name__)

# Approximate USD per 1M tokens (input, output). For cost visibility only —
# verify against current provider pricing. Unknown models log tokens, cost $0.
_PRICE_PER_1M_TOKENS: Dict[str, tuple] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "deepseek-chat": (0.27, 1.10),
}


# Process-local buffer of usage records awaiting persistence. The classifier has
# no DB handle, so it appends here and the pipeline drains+persists per email
# (drain_pending_usage). Soft-capped so a long non-draining process can't leak.
_PENDING_USAGE: list = []
_PENDING_USAGE_CAP = 10_000


def drain_pending_usage() -> list:
    """Return and clear the buffered LLM usage records (dicts)."""
    global _PENDING_USAGE
    records, _PENDING_USAGE = _PENDING_USAGE, []
    return records


def log_llm_usage(model: str, response, elapsed_s: float, kind: str = "classify") -> None:
    """Log token usage + approximate cost + latency, and buffer a record for the
    DB (drained by the pipeline). Best-effort: any failure is swallowed — never
    break an LLM call over telemetry.
    """
    try:
        usage = getattr(response, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        price_in, price_out = _PRICE_PER_1M_TOKENS.get(model, (0.0, 0.0))
        cost = pt / 1_000_000 * price_in + ct / 1_000_000 * price_out
        LOG.info(
            "LLM %s: model=%s tokens=%d in/%d out cost~$%.5f latency=%.2fs",
            kind, model, pt, ct, cost, elapsed_s,
        )
        if len(_PENDING_USAGE) < _PENDING_USAGE_CAP:
            _PENDING_USAGE.append({
                "ts": int(time.time()),
                "model": model,
                "kind": kind,
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "cost_usd": round(cost, 6),
                "latency_ms": int(elapsed_s * 1000),
            })
    except Exception:
        LOG.debug("log_llm_usage failed", exc_info=True)


@dataclass
class LLMPrediction:
    """Structured result from LLM classification.

    All fields are validated before instantiation — never store raw API output.
    """
    label: str
    confidence: float  # 0.0–1.0
    rationale: str
    action_hint: Optional[str] = None
    needs_review: bool = False


class LLMClassifier:
    """OpenAI-based LLM classifier for third-tier fallback.

    Uses lazy imports so MailMind still works if openai is not installed.
    """

    SYSTEM_PROMPT = (
        "You triage a busy person's email at a Hungarian scouting organization "
        "(Magyar Cserkészszövetség). Emails are often in Hungarian — classify by "
        "MEANING, not language. Much internal coordination happens over mailing "
        "lists, so a List-Unsubscribe footer or many recipients does NOT make a "
        "message a newsletter; judge by the actual content and intent.\n\n"
        "Choose EXACTLY ONE label, using these definitions (prefer the most "
        "specific, actionable one):\n"
        "- ACTION_REQUIRED: a person asks the reader to DO something concrete — "
        "reply, decide, fill a form, send/deliver/arrange something, a deadline.\n"
        "- MEETING: about scheduling or attending a meeting/call/event — an "
        "invitation or coordinating a time. Not generic logistics.\n"
        "- PERSONAL: a human writing to humans — discussion, coordination, "
        "relationships. Replies (Re:/Fwd:) and list threads between people are "
        "PERSONAL (or ACTION_REQUIRED/MEETING/FINANCE), never NEWSLETTER.\n"
        "- FINANCE: invoices, payments, billing, settlements (elszámolás), "
        "money/accounting, orders with amounts.\n"
        "- RECEIPT: confirmation of a completed payment/order/registration.\n"
        "- NEWSLETTER: a periodic BROADCAST publication (hírlevél, digest) — "
        "one-to-many editorial content, not addressed to the reader personally.\n"
        "- MASS_EMAIL: a bulk announcement/broadcast that isn't an editorial "
        "newsletter and isn't a personal message.\n"
        "- NOTIFICATION: an automated system message (delivery/shipping updates, "
        "file shares, app alerts) needing no human action.\n"
        "- SPAM: unsolicited junk/marketing the reader didn't ask for.\n"
        "- OTHER: none of the above fits.\n\n"
        "Example: subject 'Re: [bcs-lista] Tábori KM' with people discussing camp "
        "logistics → PERSONAL (a list thread between humans), NOT NEWSLETTER. "
        "Subject 'ECSET Heti Hírlevél' with editorial sections → NEWSLETTER.\n\n"
        "Respond ONLY with valid JSON matching this exact schema:\n"
        '{\n'
        '  "label": "<one of the labels above>",\n'
        '  "confidence": <float 0.0-1.0>,\n'
        '  "rationale": "<one sentence max>",\n'
        '  "action_hint": "<brief action if needed, else null>",\n'
        '  "needs_review": <true|false>\n'
        "}\n"
        "Rules:\n"
        "- needs_review = true only for PERSONAL, ACTION_REQUIRED, FINANCE, MEETING\n"
        "- confidence reflects genuine certainty (lower it when the content is "
        "ambiguous); do not default to a fixed value\n"
        "- never output any field outside the schema"
    )

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        max_body_chars: int = 500,
    ):
        """Initialize the LLM classifier.

        Args:
            api_key: OpenAI API key.
            model: OpenAI model name (default: gpt-4o-mini).
            max_body_chars: Max characters to include from email body (default: 500,
                matching the project's privacy invariant — never send the full body).
        """
        self.api_key = api_key
        self.model = model
        self.max_body_chars = max_body_chars

    def classify(
        self,
        sender: str,
        subject: str,
        snippet: str,
        body_text: str,
        gmail_id: str = "",
    ) -> Optional[LLMPrediction]:
        """Classify an email using the LLM.

        Builds a compact feature bundle, calls OpenAI, parses JSON response,
        validates fields, and returns an LLMPrediction or None on failure.

        Args:
            sender: Email sender address.
            subject: Email subject line.
            snippet: Email snippet/preview.
            body_text: Full or partial email body text.
            gmail_id: Optional Gmail ID for logging.

        Returns:
            LLMPrediction if successful and valid, None otherwise.
        """
        try:
            # Lazy import to avoid hard dependency
            import openai
        except ImportError:
            LOG.error("openai package is not installed. LLM classification unavailable.")
            return None

        # Build compact user prompt. Cap every free-text field that leaves the
        # machine — body (max_body_chars), subject (200), snippet (300) — so the
        # privacy invariant ("never send the full body / unbounded content") holds
        # on the OpenAI path exactly as it does on the DeepSeek path.
        body_trimmed = (body_text or "")[:self.max_body_chars]
        subject_trimmed = (subject or "")[:200]
        snippet_trimmed = (snippet or "")[:300]
        user_prompt = (
            f"Subject: {subject_trimmed or '(no subject)'}\n"
            f"From: {sender or '(unknown)'}\n"
            f"Snippet: {snippet_trimmed or '(no snippet)'}\n"
            f"Body: {body_trimmed or '(no body text)'}"
        )

        try:
            client = openai.OpenAI(api_key=self.api_key)
            _t0 = time.monotonic()
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=256,
            )
            log_llm_usage(self.model, response, time.monotonic() - _t0, kind="classify")
        except Exception as e:
            LOG.warning(
                "LLM API call failed for %s (model=%s): %s",
                gmail_id or sender, self.model, e,
            )
            return None

        # Extract and parse response
        try:
            raw = response.choices[0].message.content.strip()
            data: Dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, AttributeError, IndexError, KeyError) as e:
            LOG.warning("LLM response parse failed for %s: %s", gmail_id or sender, e)
            return None

        # --- Validation ---
        label = data.get("label", "")
        if not isinstance(label, str) or label not in VALID_LABELS:
            LOG.warning(
                "LLM returned invalid label '%s' for %s", label, gmail_id or sender
            )
            return None

        # Parse and clamp confidence
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        # Rationale: must be non-empty
        rationale = data.get("rationale", "")
        if not isinstance(rationale, str) or not rationale.strip():
            rationale = "LLM classification"

        # Action hint: optional
        action_hint = data.get("action_hint")
        if action_hint is not None and not isinstance(action_hint, str):
            action_hint = None

        # needs_review: validate and enforce rule
        needs_review = bool(data.get("needs_review", False))
        # Enforce rule: needs_review should be true for certain labels
        if label in REVIEW_LABELS:
            needs_review = True

        result = LLMPrediction(
            label=label,
            confidence=confidence,
            rationale=rationale.strip(),
            action_hint=action_hint,
            needs_review=needs_review,
        )

        LOG.info(
            "LLM classified %s: label=%s confidence=%.4f needs_review=%s",
            gmail_id or sender, label, confidence, needs_review,
        )

        return result


class OpenAIAdapter:
    """Adapter for LLMClassifier to conform to the unified LLMClassifier Protocol.

    Wraps the legacy LLMClassifier interface (which takes individual email fields)
    and adapts it to accept an Email object, returning LLMResult instead of
    LLMPrediction for compatibility with the unified Protocol.

    This allows OpenAI classification to be used interchangeably with other
    LLM providers like DeepSeek through the Protocol interface.
    """

    def __init__(self, classifier: LLMClassifier):
        """Initialize the adapter with a LLMClassifier instance.

        Args:
            classifier: The underlying OpenAI-based LLMClassifier to wrap.
        """
        self.classifier = classifier

    def classify_email(self, email: Email) -> LLMResult:
        """Classify an email using the OpenAI LLM.

        Extracts fields from the Email object and calls the underlying
        LLMClassifier, converting the result to LLMResult.

        Args:
            email: Normalized Email model with subject, sender, body_text.

        Returns:
            LLMResult with classification or fallback on failure.
        """
        prediction = self.classifier.classify(
            sender=email.sender or "",
            subject=email.subject or "",
            snippet=email.snippet or "",
            body_text=email.body_text or "",
            gmail_id=email.gmail_id or "",
        )

        if prediction is None:
            return LLMResult(model_available=False, reasoning="LLM classification failed")

        # Convert LLMPrediction to LLMResult
        return LLMResult(
            primary_label=prediction.label,
            llm_confidence=prediction.confidence,
            reasoning=prediction.rationale,
            model_available=True,
        )

    def summarize_thread(self, subject: str, body_text: str) -> str:
        """Summarize an email in 1-2 sentences via OpenAI. "" on any error.

        Mirrors DeepSeekClient.summarize_thread so the NOW-tab thread summaries
        work under LLM_PROVIDER=openai (without this the pipeline's hasattr guard
        silently skips summaries on the OpenAI path).
        """
        try:
            import openai
        except ImportError:
            return ""
        try:
            subject_preview = (subject or "")[:200]
            body_preview = (body_text or "")[:500]
            user_prompt = (
                f"Summarize this email in 1-2 sentences (max 120 chars):\n"
                f"Subject: {subject_preview}\nBody: {body_preview}"
            )
            client = openai.OpenAI(api_key=self.classifier.api_key)
            _t0 = time.monotonic()
            response = client.chat.completions.create(
                model=self.classifier.model,
                messages=[
                    {"role": "system",
                     "content": "You are a helpful assistant. Summarize emails in 1-2 sentences."},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=80,
            )
            log_llm_usage(self.classifier.model, response,
                          time.monotonic() - _t0, kind="summarize")
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            LOG.warning("OpenAI summarize_thread failed: %s", e)
            return ""