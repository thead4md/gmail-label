"""Inference orchestration for MailMind ML classification.

Provides a clean interface for running ML inference on emails as part of
the processing pipeline. Handles fallback when no model is available.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from ..storage.models import Email
from .features import extract_features, FeatureVector
from .model import MLClassifier

LOG = logging.getLogger(__name__)


@dataclass
class MLResult:
    """Result of ML inference on an email.

    This is designed to be merged into the Prediction model and scoring
    breakdown during pipeline processing.
    """
    primary_label: Optional[str] = None
    ml_confidence: Optional[float] = None
    label_probabilities: Dict[str, float] = field(default_factory=dict)
    pipeline_used: str = "rules"  # "ml" if ML contributed, "rules" otherwise
    model_available: bool = False
    error: Optional[str] = None

    def to_scoring_breakdown_entry(self) -> Dict[str, Any]:
        """Convert to a dict for appending to scoring_breakdown."""
        return {
            "ml_primary_label": self.primary_label,
            "ml_confidence": self.ml_confidence,
            "ml_pipeline_used": self.pipeline_used,
            "ml_model_available": self.model_available,
            "ml_error": self.error,
        }


def predict_label(
    email: Email,
    classifier: Optional[MLClassifier],
) -> MLResult:
    """Run ML inference on an email.

    This function:
    1. Extracts features from the email
    2. Runs ML prediction if a model is available
    3. Returns an MLResult with label, confidence, and status

    The function never raises; it always returns an MLResult with
    appropriate fallback values.

    Args:
        email: Parsed Email model.
        classifier: Optional MLClassifier instance. If None or not fitted,
            returns fallback MLResult.

    Returns:
        MLResult with prediction (or fallback).
    """
    # If no classifier or not fitted, return fallback immediately
    if classifier is None or not classifier.is_fitted:
        return MLResult(
            pipeline_used="rules",
            model_available=False,
        )

    try:
        # Build the SAME text the trainer used (content-only — no sender identity).
        # build_content_text keeps train and inference in lockstep; the sender
        # signal lives in the 20% sender channel of the blend, not here.
        from .features import build_content_text
        text_corpus = build_content_text(
            subject=getattr(email, "subject", None),
            snippet=getattr(email, "snippet", None),
            body_text=getattr(email, "body_text", None),
        )

        if not text_corpus.strip():
            LOG.debug("Empty text corpus for ML inference, returning fallback")
            return MLResult(
                pipeline_used="rules",
                model_available=True,
                error="Empty text corpus",
            )

        # Run prediction
        label, confidence = classifier.predict_single(text_corpus)
        if label is None:
            return MLResult(
                pipeline_used="rules",
                model_available=True,
                error="Prediction returned None",
            )

        # Get full probability distribution
        proba = classifier.predict_label_proba([text_corpus])

        return MLResult(
            primary_label=label,
            ml_confidence=round(confidence, 4),
            label_probabilities=proba[0] if proba else {},
            pipeline_used="ml" if confidence >= 0.3 else "rules",
            model_available=True,
        )

    except Exception as e:
        LOG.warning(f"ML inference failed for email {email.gmail_id}: {e}", exc_info=True)
        return MLResult(
            pipeline_used="rules",
            model_available=True,
            error=str(e),
        )
