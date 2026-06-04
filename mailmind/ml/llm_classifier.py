"""Third-tier LLM classifier for MailMind using OpenAI-compatible API.

Called only when rules engine and local ML model are not confident enough.
Returns strictly structured JSON every time. Never raises exceptions.

This module provides both a legacy LLMClassifier interface and an OpenAIAdapter
that conforms to the unified LLMClassifier Protocol.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from ..taxonomy import OPENAI_LABELS as VALID_LABELS, REVIEW_LABELS
from ..storage.models import Email
from ..llm.base import LLMResult

LOG = logging.getLogger(__name__)


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
        "You are an email classifier for a personal productivity assistant.\n"
        "Classify the email into exactly one of these labels:\n"
        "NEWSLETTER, NOTIFICATION, MASS_EMAIL, PERSONAL, FINANCE,\n"
        "ACTION_REQUIRED, MEETING, RECEIPT, SPAM, OTHER.\n"
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
        "- confidence reflects how certain you are, not a fixed value\n"
        "- never output any field outside the schema"
    )

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        max_body_chars: int = 1500,
    ):
        """Initialize the LLM classifier.

        Args:
            api_key: OpenAI API key.
            model: OpenAI model name (default: gpt-4o-mini).
            max_body_chars: Max characters to include from email body (default: 1500).
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

        # Build compact user prompt
        body_trimmed = (body_text or "")[:self.max_body_chars]
        user_prompt = (
            f"Subject: {subject or '(no subject)'}\n"
            f"From: {sender or '(unknown)'}\n"
            f"Snippet: {snippet or '(no snippet)'}\n"
            f"Body: {body_trimmed or '(no body text)'}"
        )

        try:
            client = openai.OpenAI(api_key=self.api_key)
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