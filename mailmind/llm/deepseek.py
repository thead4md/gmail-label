"""DeepSeek LLM client for MailMind email classification.

Provides integration with DeepSeek's chat completions API (compatible
with the OpenAI SDK) to classify emails into predefined label categories.

All external API calls use JSON mode for structured output parsing.

Design decisions:
- Uses openai SDK pointed at DeepSeek base URL (no separate DeepSeek SDK needed)
- JSON mode with response_format={"type": "json_object"}
- Timeout: 10 seconds per call
- Graceful fallback on any failure (returns LLMResult with model_available=False)
- Never sends full email body — only first 500 chars of body_text
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

from openai import OpenAI, APIError, APITimeoutError, APIConnectionError

from ..storage.models import Email
from ..config import MailMindConfig

LOG = logging.getLogger(__name__)


@dataclass
class LLMResult:
    """Result of LLM inference on an email.

    This is designed to be merged into the Prediction model and scoring
    breakdown during pipeline processing, alongside MLResult (from
    the local ML model).

    Attributes:
        primary_label: The LLM-predicted label (one of VALID_LABELS).
        llm_confidence: Confidence score 0.0 - 1.0.
        reasoning: Short explanation from the LLM (1-2 sentences).
        model_available: Whether the LLM was successfully called.
    """
    primary_label: str = "NOTIFICATION"
    llm_confidence: float = 0.0
    reasoning: str = ""
    model_available: bool = True

    def to_scoring_breakdown_entry(self) -> Dict[str, Any]:
        """Convert to a dict for appending to scoring_breakdown."""
        return {
            "label": self.primary_label,
            "confidence": self.llm_confidence,
            "reasoning": self.reasoning,
        }


class DeepSeekClient:
    """Client for DeepSeek chat completions API.

    Uses JSON mode to get structured email classifications.
    Compatible with any OpenAI-compatible API endpoint.

    Attributes:
        valid_labels: Set of accepted label values.
        model: DeepSeek model name.
        client: OpenAI-compatible client instance.
    """

    VALID_LABELS = {
        "NOTIFICATION",
        "NEWSLETTER",
        "MASS_EMAIL",
        "PERSONAL",
        "FINANCE",
        "CALENDAR",
    }

    SYSTEM_PROMPT = (
        "You are an email classifier. Classify the email into exactly one "
        "of these categories: NOTIFICATION, NEWSLETTER, MASS_EMAIL, "
        "PERSONAL, FINANCE, CALENDAR. Return JSON with fields: "
        "label (string), confidence (float 0-1), reasoning (string)."
    )

    def __init__(self, config: MailMindConfig):
        """Initialize DeepSeek client.

        Args:
            config: MailMindConfig with API key, model, and base URL.
        """
        self.model = config.deepseek_model
        self.client = OpenAI(
            api_key=config.deepseek_api_key,
            base_url=config.deepseek_base_url,
            timeout=10.0,
        )

    def classify_email(self, email: Email) -> LLMResult:
        """Classify an email using DeepSeek LLM.

        Args:
            email: Normalized Email model (only subject, sender, body_text used).

        Returns:
            LLMResult with classification or fallback on failure.
        """
        try:
            # Build user prompt (never include full body_text)
            subject = (email.subject or "")[:200]
            sender = email.sender or ""
            body_preview = (email.body_text or "")[:500]

            user_prompt = (
                f"Subject: {subject}\n"
                f"From: {sender}\n"
                f"Body: {body_preview}"
            )

            LOG.debug("Calling DeepSeek API for email %s...", email.gmail_id)

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,  # Low temp for more deterministic classification
                max_tokens=150,
            )

            content = response.choices[0].message.content
            if not content:
                LOG.warning("DeepSeek returned empty response for %s", email.gmail_id)
                return LLMResult(
                    model_available=False,
                    reasoning="Empty response from LLM",
                )

            # Parse JSON response
            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                LOG.warning(
                    "DeepSeek returned malformed JSON for %s: %s",
                    email.gmail_id, e,
                )
                return LLMResult(
                    model_available=False,
                    reasoning=f"Malformed JSON response: {e}",
                )

            label = data.get("label", "").strip().upper()
            confidence = float(data.get("confidence", 0.0))
            reasoning = str(data.get("reasoning", ""))[:200]  # Cap reasoning length

            # Validate label
            if label not in self.VALID_LABELS:
                LOG.warning(
                    "DeepSeek returned invalid label '%s' for %s",
                    label, email.gmail_id,
                )
                return LLMResult(
                    model_available=False,
                    reasoning=f"Invalid label: {label}",
                )

            # Clamp confidence to [0.0, 1.0]
            confidence = max(0.0, min(1.0, confidence))

            LOG.debug(
                "DeepSeek classified %s: label=%s confidence=%.4f",
                email.gmail_id, label, confidence,
            )

            return LLMResult(
                primary_label=label,
                llm_confidence=confidence,
                reasoning=reasoning,
                model_available=True,
            )

        except APITimeoutError:
            LOG.warning("DeepSeek API timeout for %s", email.gmail_id)
            return LLMResult(
                model_available=False,
                reasoning="API timeout",
            )

        except APIConnectionError as e:
            LOG.warning("DeepSeek API connection error for %s: %s", email.gmail_id, e)
            return LLMResult(
                model_available=False,
                reasoning="API connection error",
            )

        except APIError as e:
            LOG.warning("DeepSeek API error for %s: %s", email.gmail_id, e)
            return LLMResult(
                model_available=False,
                reasoning=f"API error: {e}",
            )

        except Exception as e:
            LOG.warning(
                "Unexpected DeepSeek error for %s: %s",
                email.gmail_id, e, exc_info=True,
            )
            return LLMResult(
                model_available=False,
                reasoning=f"Unexpected error: {e}",
            )
