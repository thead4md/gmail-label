"""Routing logic that decides which tier handles each email.

Tiers:
  TIER 1: Rules engine - high-confidence deterministic labels
  TIER 2: Local sklearn model - medium-confidence ML labels
  TIER 3: LLM classifier - called only when tiers 1+2 are not confident

The router is fully configurable via thresholds and a global enable flag.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from ..storage.models import Email
from .llm_classifier import LLMClassifier, LLMPrediction

if TYPE_CHECKING:
    from ..processing.rules import RulesEngine
    from ..ml.inference import MLResult
    from ..ml.model import MLClassifier

LOG = logging.getLogger(__name__)


@dataclass
class RoutingResult:
    """Result of routing an email through the classifier tiers."""
    source: str  # "rules" | "ml" | "llm" | "fallback"
    label: str
    confidence: float
    llm_prediction: Optional[LLMPrediction] = None


class ClassifierRouter:
    """Routes emails through classifier tiers based on confidence thresholds.

    Decides which tier handles each email:
    1. Rules engine - if confident enough, done
    2. ML model - if confident enough AND label != "OTHER", done
    3. LLM classifier - if enabled and not already handled
    4. Fallback - best available result
    """

    def __init__(
        self,
        rules_engine: "RulesEngine",
        ml_model: Optional["MLClassifier"] = None,
        llm_classifier: Optional[LLMClassifier] = None,
        rules_threshold: float = 0.90,
        ml_threshold: float = 0.65,
        llm_enabled: bool = True,
    ):
        self.rules_engine = rules_engine
        self.ml_model = ml_model
        self.llm_classifier = llm_classifier
        self.rules_threshold = rules_threshold
        self.ml_threshold = ml_threshold
        self.llm_enabled = llm_enabled

    def route(self, email: Email, rule_matches: Optional[list] = None) -> RoutingResult:
        """Route an email through the classifier tiers.

        rule_matches: pre-computed RulesEngine.evaluate(email) result. When the
        caller (Pipeline.process) already ran the rules, pass them in to avoid a
        redundant second evaluation. None → evaluate here (back-compat for any
        direct callers / tests).
        """
        # --- TIER 1: Rules Engine ---
        if rule_matches is None:
            rule_matches = self.rules_engine.evaluate(email)
        matched_rules = [m for m in rule_matches if m.matched]
        rules_label, rules_confidence = self._extract_rules_result(matched_rules, email)

        if rules_confidence >= self.rules_threshold:
            LOG.debug(
                "Tier 1 (rules) handles email %s: label=%s confidence=%.4f",
                email.gmail_id, rules_label, rules_confidence,
            )
            return RoutingResult(
                source="rules",
                label=rules_label,
                confidence=min(rules_confidence, 1.0),
            )

        # --- TIER 2: ML Model ---
        ml_label: Optional[str] = None
        ml_confidence: float = 0.0
        ml_available = False

        if self.ml_model is not None and self.ml_model.is_fitted:
            try:
                from ..ml.inference import predict_label
                ml_result: MLResult = predict_label(email, self.ml_model)
                if ml_result.model_available and ml_result.primary_label is not None:
                    ml_available = True
                    ml_label = ml_result.primary_label
                    ml_confidence = ml_result.ml_confidence or 0.0
            except Exception as e:
                LOG.warning("ML inference failed for %s: %s", email.gmail_id, e)

        if ml_available and ml_confidence >= self.ml_threshold and ml_label != "OTHER":
            LOG.debug(
                "Tier 2 (ML) handles email %s: label=%s confidence=%.4f",
                email.gmail_id, ml_label, ml_confidence,
            )
            return RoutingResult(
                source="ml",
                label=ml_label,
                confidence=min(ml_confidence, 1.0),
            )

        # --- TIER 3: LLM Classifier ---
        if self.llm_enabled and self.llm_classifier is not None:
            LOG.debug(
                "Tier 3 (LLM) invoked for email %s: rules_confidence=%.4f, "
                "ml_label=%s ml_confidence=%.4f",
                email.gmail_id, rules_confidence, ml_label or "N/A", ml_confidence,
            )
            llm_prediction = self.llm_classifier.classify(
                sender=email.sender or "",
                subject=email.subject or "",
                snippet=email.snippet or "",
                body_text=email.body_text or "",
                gmail_id=email.gmail_id or "",
            )
            if llm_prediction is not None:
                LOG.info(
                    "Tier 3 (LLM) handles email %s: label=%s confidence=%.4f",
                    email.gmail_id, llm_prediction.label, llm_prediction.confidence,
                )
                return RoutingResult(
                    source="llm",
                    label=llm_prediction.label,
                    confidence=llm_prediction.confidence,
                    llm_prediction=llm_prediction,
                )

        # --- FALLBACK: best available result ---
        if ml_available:
            LOG.debug(
                "Fallback to ML for email %s: label=%s confidence=%.4f",
                email.gmail_id, ml_label, ml_confidence,
            )
            return RoutingResult(
                source="fallback",
                label=ml_label or "OTHER",
                confidence=ml_confidence,
            )

        LOG.debug(
            "Fallback to rules for email %s: label=%s confidence=%.4f",
            email.gmail_id, rules_label, rules_confidence,
        )
        return RoutingResult(
            source="fallback",
            label=rules_label,
            confidence=rules_confidence,
        )

    def _extract_rules_result(
        self, matched_rules: list, email: Email,
    ) -> tuple[str, float]:
        """Extract label and confidence from matched rules."""
        from ..processing.scorer import PriorityScorer
        primary_label = PriorityScorer._determine_primary_label(email, matched_rules)
        confidence = 0.0
        for match in matched_rules:
            if match.matched and match.confidence > confidence:
                confidence = match.confidence
        if not matched_rules:
            confidence = 0.0
            primary_label = "NOTIFICATION"
        return primary_label, min(confidence, 0.95)
