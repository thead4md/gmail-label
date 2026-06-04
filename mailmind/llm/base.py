"""Base protocol and result types for LLM classifiers.

Defines the LLMClassifier Protocol that all LLM-based classification clients
must implement, allowing for flexible use of different LLM providers (DeepSeek,
OpenAI, etc.) with a unified interface.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol

from ..storage.models import Email


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


class LLMClassifier(Protocol):
    """Protocol for LLM-based email classification clients.

    Any LLM client must implement this interface to be used in the
    unified classification pipeline. Allows swapping between different
    LLM providers (DeepSeek, OpenAI, etc.) without changing calling code.
    """

    def classify_email(self, email: Email) -> LLMResult:
        """Classify an email using the LLM.

        Args:
            email: Normalized Email model with subject, sender, body_text.

        Returns:
            LLMResult with classification or fallback on failure.
            model_available=False indicates the LLM could not be called.
        """
        ...

    # def summarize(self, text: str) -> str:
    #     """Summarize email text using the LLM.
    #
    #     Optional method for future expansion. Not yet implemented.
    #
    #     Args:
    #         text: Email text to summarize.
    #
    #     Returns:
    #         Summary string.
    #     """
    #     ...
