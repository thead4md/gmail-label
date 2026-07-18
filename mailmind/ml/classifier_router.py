"""Routing logic that decides which tier handles each email.

Tiers:
  TIER 1: Rules engine - high-confidence deterministic labels
  TIER 2: Local sklearn model - medium-confidence ML labels
  TIER 3: LLM classifier - called only when tiers 1+2 are not confident

The router is fully configurable via thresholds and a global enable flag.
Uses the unified LLMClassifier Protocol, allowing any LLM provider
(DeepSeek, OpenAI, etc.) to be used as tier 3.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from ..storage.models import Email
from ..llm.base import LLMClassifier, LLMResult

if TYPE_CHECKING:
    from ..processing.rules import RulesEngine
    from ..ml.inference import MLResult
    from ..ml.model import MLClassifier
    from ..storage.database import Database

LOG = logging.getLogger(__name__)


@dataclass
class RoutingResult:
    """Result of routing an email through the classifier tiers."""
    source: str  # "rule" | "rules" | "ml" | "llm" | "blend" | "fallback"
    label: str
    # None only in the "fallback" source when there is genuinely no real signal
    # (no labeling rule matched, no ML, no usable LLM) — see route()'s final
    # return. Every other tier always carries a real (possibly still low) float.
    confidence: Optional[float]
    llm_result: Optional[LLMResult] = None
    # Blend audit: channel distributions before blending
    content_distribution: Optional[dict] = field(default=None)
    sender_distribution: Optional[dict] = field(default=None)


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
        # 80/20 blend parameters
        blend_enabled: bool = True,
        content_weight: float = 0.80,
        sender_weight: float = 0.20,
        sender_prior_min_count: int = 3,
        fold_rules_into_content: bool = False,
        content_rule_weight: float = 0.30,
    ):
        self.rules_engine = rules_engine
        self.ml_model = ml_model
        self.llm_classifier = llm_classifier
        self.rules_threshold = rules_threshold
        self.ml_threshold = ml_threshold
        self.llm_enabled = llm_enabled
        self.blend_enabled = blend_enabled
        self.content_weight = content_weight
        self.sender_weight = sender_weight
        self.sender_prior_min_count = sender_prior_min_count
        self.fold_rules_into_content = fold_rules_into_content
        self.content_rule_weight = content_rule_weight

    def route(
        self,
        email: Email,
        rule_matches: Optional[list] = None,
        db: Optional["Database"] = None,
        account: Optional[str] = None,
    ) -> RoutingResult:
        """Route an email through the classifier tiers.

        rule_matches: pre-computed RulesEngine.evaluate(email) result. When the
        caller (Pipeline.process) already ran the rules, pass them in to avoid a
        redundant second evaluation. None → evaluate here (back-compat for any
        direct callers / tests).

        db, account: passed by Pipeline.process to check sender/thread label rules.
        When db is provided, label rules (created by user feedback) are checked first.
        """
        # --- TIER 0: User-defined label rules (sender + thread) ---
        if db is not None:
            from ..storage.queries import resolve_sender_label, get_thread_label

            # Check thread rule first (more specific)
            if email.thread_id:
                thread_label = get_thread_label(db, email.thread_id)
                if thread_label:
                    LOG.debug(
                        "Tier 0 (thread rule) handles email %s: label=%s",
                        email.gmail_id, thread_label,
                    )
                    return RoutingResult(
                        source="rule",
                        label=thread_label,
                        confidence=1.0,
                    )

            # Check sender rules (conditional subject-pattern rules are evaluated
            # against the subject; a non-matching conditional rule returns None so
            # the email falls through to content classification below).
            if email.sender:
                sender_label = resolve_sender_label(
                    db, email.sender, email.subject, account=account
                )
                if sender_label:
                    LOG.debug(
                        "Tier 0 (sender rule) handles email %s: label=%s",
                        email.gmail_id, sender_label,
                    )
                    return RoutingResult(
                        source="rule",
                        label=sender_label,
                        confidence=1.0,
                    )

        # --- TIER 1: Rules Engine (content rules) ---
        if rule_matches is None:
            rule_matches = self.rules_engine.evaluate(email)
        matched_rules = [m for m in rule_matches if m.matched]
        rules_label, rules_confidence = self._extract_rules_result(matched_rules, email)

        # Tier-1 hard short-circuit. Disabled when fold_rules_into_content is on:
        # in that mode a confident content rule no longer pre-empts the blend; it
        # instead contributes a weighted vote to the content distribution below.
        if not self.fold_rules_into_content and rules_confidence >= self.rules_threshold:
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
        ml_label_probabilities: dict = {}

        if self.ml_model is not None and self.ml_model.is_fitted:
            try:
                from ..ml.inference import predict_label
                ml_result: MLResult = predict_label(email, self.ml_model)
                if ml_result.model_available and ml_result.primary_label is not None:
                    ml_available = True
                    ml_label = ml_result.primary_label
                    ml_confidence = ml_result.ml_confidence or 0.0
                    ml_label_probabilities = ml_result.label_probabilities or {}
            except Exception as e:
                LOG.warning("ML inference failed for %s: %s", email.gmail_id, e)

        # --- BLEND PATH (80% content / 20% sender) ---
        # Build the CONTENT-channel distribution:
        #   * legacy (fold off): content = ML proba, and only when ML clears the
        #     ml_threshold floor (Bug #1 fix). A confident content rule has already
        #     short-circuited above via Tier 1.
        #   * fold on: content = ML proba + content-rule vote (build_content_distribution),
        #     so a confident rule contributes to — rather than pre-empts — the blend.
        if self.blend_enabled:
            from .sender_channel import (
                build_sender_distribution,
                blend_distributions,
                build_content_distribution,
            )
            if self.fold_rules_into_content:
                p_content = build_content_distribution(
                    ml_proba=ml_label_probabilities,
                    rules_label=rules_label,
                    rules_confidence=rules_confidence,
                    rule_weight=self.content_rule_weight,
                )
            else:
                p_content = (
                    ml_label_probabilities
                    if (ml_available and ml_confidence >= self.ml_threshold)
                    else {}
                )

            if p_content:
                p_sender = build_sender_distribution(
                    sender=email.sender or "",
                    db=db,
                    account=account,
                    min_count=self.sender_prior_min_count,
                )
                p_blended = blend_distributions(
                    p_content=p_content,
                    p_sender=p_sender,
                    content_weight=self.content_weight,
                    sender_weight=self.sender_weight,
                )
                blend_label = max(p_blended, key=p_blended.get)
                blend_confidence = p_blended[blend_label]
                # Bug #3 fix: an OTHER winner falls through to LLM / fallback. We do
                # NOT impose an absolute confidence floor here — content distributions
                # spread mass across many classes, so a valid label often tops out
                # below ml_threshold. The pipeline escalates low-confidence blends to
                # the LLM via LLM_FALLBACK_FLOOR, so a second router-level gate would
                # only dump good content labels to generic fallback.
                if blend_label == "OTHER":
                    LOG.debug(
                        "Blend for %s → OTHER(%.2f); falling through to cascade/LLM.",
                        email.gmail_id, blend_confidence,
                    )
                else:
                    LOG.info(
                        "Blend for %s: content=%s(%.2f) sender=%s → %s(%.2f) [sender_abstained=%s]",
                        email.gmail_id, ml_label or rules_label, ml_confidence,
                        "abstained" if not p_sender else str(sorted(p_sender.items())),
                        blend_label, blend_confidence, not p_sender,
                    )
                    return RoutingResult(
                        source="blend",
                        label=blend_label,
                        confidence=round(blend_confidence, 4),
                        content_distribution=p_content,
                        sender_distribution=p_sender or None,
                    )

        # --- NON-BLEND: old cascade (blend_enabled=False or no ML) ---
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
            llm_result = self.llm_classifier.classify_email(email)
            if llm_result.model_available:
                LOG.info(
                    "Tier 3 (LLM) handles email %s: label=%s confidence=%.4f",
                    email.gmail_id, llm_result.primary_label, llm_result.llm_confidence,
                )
                return RoutingResult(
                    source="llm",
                    label=llm_result.primary_label,
                    confidence=llm_result.llm_confidence,
                    llm_result=llm_result,
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
        # rules_confidence == 0.0 here is the bare _extract_rules_result sentinel
        # for "no labeling rule matched". Combined with ml_available being False
        # (checked above) and Tier 3 already having returned above if the LLM had
        # a usable result, reaching this point with rules_confidence == 0.0 means
        # nobody had an opinion at all. Surface that as confidence=None (not 0.0)
        # so it can't be mistaken downstream for a real, confident-but-low 0.0
        # score. That exact collision made Pipeline._create_prediction's
        # `next((c for c in (..., routed_conf) if c is not None), 0.85)` fallback
        # never fire — 0.0 is not None, so it passed through verbatim — and
        # QueueManager then saw 0.0 < 0.65 and silently dropped the email instead
        # of queuing it for human review.
        no_real_signal = rules_confidence <= 0.0
        return RoutingResult(
            source="fallback",
            label=rules_label,
            confidence=None if no_real_signal else rules_confidence,
        )

    def _extract_rules_result(
        self, matched_rules: list, email: Email,
    ) -> tuple[str, float]:
        """Extract label and confidence from matched rules.

        Only rules that actually *assign a label* may drive the Tier-1
        short-circuit confidence. Label-free rules (e.g. ``directly_addressed``,
        which fires at 0.95 whenever the user is in To:) are priority-*score*
        signals, not classifications — counting their confidence here would let
        a content-free signal pre-empt the ML/LLM content tiers and default the
        email to NOTIFICATION. Their score contribution is unchanged (it happens
        in the scorer); they simply no longer gate classification.
        """
        from ..processing.scorer import PriorityScorer
        primary_label = PriorityScorer._determine_primary_label(email, matched_rules)
        labeling_matches = [m for m in matched_rules if m.matched and m.labels]
        confidence = max((m.confidence for m in labeling_matches), default=0.0)
        if not matched_rules:
            primary_label = "NOTIFICATION"
        return primary_label, min(confidence, 0.95)
