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
from typing import Optional

from openai import OpenAI, APIError, APITimeoutError, APIConnectionError

from ..storage.models import Email
from ..config import MailMindConfig
from ..taxonomy import DEEPSEEK_LABELS
from .base import LLMResult

LOG = logging.getLogger(__name__)


class DeepSeekClient:
    """Client for DeepSeek chat completions API.

    Uses JSON mode to get structured email classifications.
    Compatible with any OpenAI-compatible API endpoint.

    Attributes:
        valid_labels: Set of accepted label values.
        model: DeepSeek model name.
        client: OpenAI-compatible client instance.
    """

    VALID_LABELS = DEEPSEEK_LABELS

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

            # `or ""` / `or 0.0` guard against an explicit JSON null value — a
            # plain .get(key, default) only applies the default when the key is
            # ABSENT, so {"label": null} would otherwise crash on None.strip().
            label = (data.get("label") or "").strip().upper()
            confidence = float(data.get("confidence") or 0.0)
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

    def summarize_thread(self, subject: str, body_text: str) -> str:
        """Summarize an email thread in 1-2 sentences.

        Args:
            subject: Email subject line.
            body_text: Email body text (will be capped at 500 chars).

        Returns:
            Plain-text summary (1-2 sentences, up to 120 chars), or "" on any error.
        """
        try:
            subject_preview = (subject or "")[:200]
            body_preview = (body_text or "")[:500]

            user_prompt = (
                f"Summarize this email in 1-2 sentences (max 120 chars):\n"
                f"Subject: {subject_preview}\n"
                f"Body: {body_preview}"
            )

            LOG.debug("Calling DeepSeek API to summarize thread: %s...", subject_preview[:60])

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful assistant. Summarize emails in 1-2 sentences.",
                    },
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=80,
            )

            content = response.choices[0].message.content
            if not content:
                LOG.warning("DeepSeek returned empty response for summarize_thread")
                return ""

            summary = content.strip()[:120]
            LOG.debug("Thread summary: %s", summary)
            return summary

        except APITimeoutError:
            LOG.warning("DeepSeek API timeout for summarize_thread")
            return ""

        except APIConnectionError as e:
            LOG.warning("DeepSeek API connection error for summarize_thread: %s", e)
            return ""

        except APIError as e:
            LOG.warning("DeepSeek API error for summarize_thread: %s", e)
            return ""

        except Exception as e:
            LOG.warning("Unexpected error in summarize_thread: %s", e, exc_info=True)
            return ""
