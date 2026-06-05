"""MailMind processing pipeline: orchestrates rules, scoring, and actions.

This is the core MVP deterministic pipeline:
Email -> Rules -> Score -> Prediction Persistence -> Optional Safe Action

For MVP, the pipeline uses only rules-based classification.
ML/LLM stages are stubbed as TODO extension points for future phases.
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

import json

from ..storage.models import Email, Prediction
from ..storage.database import Database
from .rules import RulesEngine
from .scorer import PriorityScorer, ScoreResult
from ..actions.safety import SafetyPolicy
from ..ml.classifier_router import ClassifierRouter, RoutingResult
from ..intelligence.thread_analyzer import ThreadAnalyzer
from ..intelligence.channels import detect_channel

if TYPE_CHECKING:
    from ..ml.inference import MLResult
    from ..actions.executor import ActionExecutor

LOG = logging.getLogger(__name__)


def resolve_label_precedence(
    base_label: str,
    *,
    ml_confident: bool,
    llm_present: bool,
    llm_label: Optional[str],
    llm_confidence: Optional[float],
    llm_override_floor: float,
    llm_fallback_floor: float,
    routing_source: Optional[str],
    routing_label: Optional[str],
) -> tuple:
    """Single source of truth for (primary_label, classifier_source, pipeline_used).

    This is where the rules→ML→LLM→router label precedence lives — historically
    the spot where "silent override" bugs kept appearing, so it is now one pure,
    independently-tested function. Precedence, highest first:

      1. Router classified via a real tier ('rule'/'rules'/'ml'/'llm') → that label.
      2. Router 'fallback' AND a usable LLM label (conf ≥ fallback floor) → LLM label.
      3. Router 'fallback' AND no usable LLM → the router's fallback label.
      4. No router AND a confident LLM (conf ≥ override floor) → LLM label.
      5. Otherwise → the rules-score base label.

    pipeline_used becomes 'hybrid' whenever ML or LLM contributed; classifier_source
    defaults to 'rules' until the router assigns a tier.
    """
    primary = base_label
    source = "rules"
    pipeline = "rules"

    if ml_confident:
        pipeline = "hybrid"

    if llm_present:
        pipeline = "hybrid"
        if llm_confidence is not None and llm_confidence >= llm_override_floor:
            primary = llm_label  # case 4

    llm_usable = (
        llm_present and llm_confidence is not None
        and llm_confidence >= llm_fallback_floor
    )

    if routing_source is not None:
        if routing_source == "fallback" and llm_usable:
            primary = llm_label              # case 2
            source = "llm"
            pipeline = "hybrid"
        else:
            source = routing_source          # cases 1 & 3
            primary = routing_label
            pipeline = routing_source

    return primary, source, pipeline


class Pipeline:
    """Deterministic MVP processing pipeline.

    Orchestrates:
    1. Rule matching
    2. Priority scoring
    3. Prediction persistence
    4. Optional safe action execution
    """

    ML_CONFIDENCE_THRESHOLD = 0.3
    LLM_CONFIDENCE_OVERRIDE = 0.90
    # In the fallback path (no confident rules/ML label) the router only has a
    # ~0-confidence guess, so a usable LLM content label should win even below the
    # strict 0.90 override. Floor avoids letting a very-unsure LLM label through.
    LLM_FALLBACK_FLOOR = 0.65

    def __init__(
        self,
        db: Database,
        rules_engine: RulesEngine,
        scorer: PriorityScorer,
        executor: Optional["ActionExecutor"] = None,
        safety_policy: Optional[SafetyPolicy] = None,
        llm_client: Optional["DeepSeekClient"] = None,
        llm_skip_threshold: int = 70,
        llm_max_calls_per_run: int = 10,
        classifier_router: Optional[ClassifierRouter] = None,
    ):
        """Initialize pipeline.

        Args:
            db: Database instance for persistence.
            rules_engine: RulesEngine for classification.
            scorer: PriorityScorer for scoring.
            executor: Optional ActionExecutor for applying actions.
            safety_policy: Optional SafetyPolicy for action decisions.
            llm_client: Optional DeepSeekClient for LLM classification (Pass 7+).
            llm_skip_threshold: Skip LLM if rules score >= this value (default 70).
            llm_max_calls_per_run: Max LLM API calls per pipeline run (default 10).
            classifier_router: Optional ClassifierRouter for tiered classification.
        """
        self.db = db
        self.rules_engine = rules_engine
        self.scorer = scorer
        self.executor = executor
        self.safety_policy = safety_policy or SafetyPolicy(dry_run=True)
        self.llm_client = llm_client
        self.llm_skip_threshold = llm_skip_threshold
        self.llm_max_calls_per_run = llm_max_calls_per_run
        self.llm_confidence_override = self.LLM_CONFIDENCE_OVERRIDE
        self._llm_calls_this_run: int = 0
        self.classifier_router = classifier_router

    def process(
        self,
        email: Email,
        auto_action: bool = False,
        account: Optional[str] = None,
    ) -> Prediction:
        """Process an email through the full pipeline."""
        LOG.info(f"Processing email {email.gmail_id} from {email.sender}")

        rule_matches = self.rules_engine.evaluate(email)
        matched_rules = [m for m in rule_matches if m.matched]
        LOG.debug(f"Matched {len(matched_rules)} rules: {[m.rule_name for m in matched_rules]}")

        score = self.scorer.compute_score(email, rule_matches)
        LOG.debug(f"Score: {score.total_score} (primary_label: {score.primary_label})")
        LOG.debug(f"Breakdown:\n{score.breakdown_text}")

        final_labels = list(set(email.labels or []))
        for match in matched_rules:
            if match.labels:
                final_labels.extend(match.labels)
        final_labels = list(set(final_labels))

        suggested_action = self.executor.suggest_action(email, score) if self.executor else None

        # 3a. Run classifier router (OpenAI LLM third-tier fallback) if available
        routing_result: Optional[RoutingResult] = None
        if self.classifier_router is not None:
            # Reuse the rules already evaluated above (line ~90) instead of
            # letting route() run them a second time.
            routing_result = self.classifier_router.route(
                email, rule_matches=rule_matches, db=self.db, account=account
            )
            if routing_result is not None:
                LOG.info(
                    "email %s classified by %s \u2192 %s (%.2f)",
                    email.gmail_id,
                    routing_result.source,
                    routing_result.label,
                    routing_result.confidence,
                )

        # 3b. Run LLM classification (Pass 7+ DeepSeek) if applicable.
        # Skip the paid LLM when:
        #   - rules score is high enough, OR
        #   - the router already handled this email via rules or ML (cheap tiers
        #     succeeded; calling LLM would be wasted spend).
        # "rule" = a Tier-0 user override (explicit, confidence 1.0) — honour it and
        # never spend a paid LLM call on it. "rules"/"ml" = a cheaper tier already
        # classified confidently. Only the low-confidence "fallback" (or no router)
        # should reach the LLM.
        ml_or_rules_handled = (
            routing_result is not None
            and routing_result.source in ("rule", "rules", "ml")
        )
        llm_result = None
        if (
            self.llm_client is not None
            and self._llm_calls_this_run < self.llm_max_calls_per_run
            and score.total_score < self.llm_skip_threshold
            and not ml_or_rules_handled
        ):
            llm_result = self.llm_client.classify_email(email)
            self._llm_calls_this_run += 1
            LOG.info(
                "LLM classified %s: label=%s confidence=%.2f (call %d/%d)",
                email.gmail_id,
                llm_result.primary_label,
                llm_result.llm_confidence,
                self._llm_calls_this_run,
                self.llm_max_calls_per_run,
            )
        elif ml_or_rules_handled:
            LOG.debug(
                "Skipping LLM for %s: router handled via %s (conf=%.2f)",
                email.gmail_id, routing_result.source, routing_result.confidence,
            )
        elif score.total_score >= self.llm_skip_threshold:
            LOG.debug(
                "Skipping LLM for %s: rules score %d >= threshold %d",
                email.gmail_id, score.total_score, self.llm_skip_threshold,
            )

        prediction = self._create_prediction(
            email, score, final_labels, matched_rules,
            suggested_action=suggested_action,
            llm_result=llm_result,
            routing_result=routing_result,
        )

        # Analyze thread context and persist into prediction if possible
        try:
            thread_ctx = ThreadAnalyzer.analyze(email, self.db)
            LOG.debug("Thread analysis result: %s", thread_ctx)

            # Generate LLM summary for reply-needed threads (if budget allows)
            if (
                thread_ctx.reply_needed
                and self.llm_client is not None
                and hasattr(self.llm_client, "summarize_thread")
                and self._llm_calls_this_run < self.llm_max_calls_per_run
            ):
                summary = self.llm_client.summarize_thread(
                    subject=email.subject or "",
                    body_text=email.body_text or "",
                )
                if summary:
                    thread_ctx.thread_summary = summary
                    self._llm_calls_this_run += 1
                    LOG.debug(
                        "LLM thread summary generated for %s (call %d/%d)",
                        email.gmail_id,
                        self._llm_calls_this_run,
                        self.llm_max_calls_per_run,
                    )

            # attach thread context JSON to prediction
            prediction.thread_context_json = json.dumps(thread_ctx.__dict__)
            LOG.debug("Attached thread_context_json: %s", prediction.thread_context_json)
        except Exception as e:
            LOG.debug("Thread analysis failed: %s", e)

        # Detect communication channel (newsletter / transactional / team / …)
        try:
            user_domain = None
            sender = getattr(email, "sender", None) or ""
            # derive the user's own domain from the account this email belongs to
            acct = getattr(prediction, "account", None) or getattr(email, "account", None)
            if acct and "@" in acct:
                user_domain = acct.split("@", 1)[1]
            prediction.channel = detect_channel(
                subject=getattr(email, "subject", None),
                sender=sender,
                body_text=getattr(email, "body_text", None),
                user_domain=user_domain,
            )
            LOG.debug("Detected channel: %s", prediction.channel)
        except Exception as e:
            LOG.debug("Channel detection failed: %s", e)

        try:
            prediction.id = self.db.save_prediction(prediction)
            # Log the FINAL persisted label (prediction.primary_label), not the
            # scorer's intermediate label — they differ whenever ML/LLM/router won.
            LOG.info(
                "Prediction persisted for %s: score=%d, label=%s (source=%s)",
                email.gmail_id, score.total_score,
                prediction.primary_label, prediction.classifier_source,
            )
        except Exception as e:
            LOG.error(f"Failed to persist prediction: {e}", exc_info=True)

        # Persist any LLM token/cost usage buffered during this email's processing
        # (the classifier has no DB handle; it buffers, the pipeline drains).
        try:
            from ..ml.llm_classifier import drain_pending_usage
            from ..storage.queries import record_llm_usage
            record_llm_usage(self.db, drain_pending_usage())
        except Exception:
            LOG.debug("LLM usage persistence failed", exc_info=True)

        if auto_action and self.executor:
            suggested_action = self.executor.suggest_action(email, score)
            if suggested_action:
                LOG.debug(f"Suggested action: {suggested_action} (score: {score.total_score})")
                executed = self.executor.execute_action(
                    email, suggested_action, score,
                    confidence=getattr(prediction, "confidence", None),
                )
                if executed:
                    LOG.info(f"Action '{suggested_action}' executed on {email.gmail_id}")
                else:
                    LOG.debug(f"Action '{suggested_action}' was not executed (blocked by policy or low confidence)")
            else:
                LOG.debug(f"No suggested action for {email.gmail_id} (score: {score.total_score})")
        elif auto_action:
            LOG.debug("auto_action=True but no executor available")

        return prediction

    def _create_prediction(
        self,
        email: Email,
        score: ScoreResult,
        labels: list,
        matched_rules: list,
        ml_result: Optional["MLResult"] = None,
        suggested_action: Optional[str] = None,
        llm_result: Optional["LLMResult"] = None,
        routing_result: Optional[RoutingResult] = None,
    ) -> Prediction:
        """Create a Prediction model combining rules + optional ML/LLM results."""
        import json

        breakdown_dict = score.to_dict()
        scoring_breakdown = json.dumps(breakdown_dict)
        rule_names = [m.rule_name for m in matched_rules]
        final_labels = list(labels)
        ml_confidence = None
        priority_score = score.total_score

        # --- Gather per-tier signals + their breakdown/label side-effects. The
        # actual label precedence is decided once, below, by
        # resolve_label_precedence (no primary_label mutation scattered here). ---
        ml_confident = False
        if ml_result and ml_result.model_available and ml_result.primary_label is not None:
            ml_confidence = ml_result.ml_confidence
            ml_confident = (
                ml_confidence is not None and ml_confidence >= self.ML_CONFIDENCE_THRESHOLD
            )
            if ml_confident and ml_result.primary_label not in final_labels:
                final_labels.append(ml_result.primary_label)
            breakdown_dict["ml"] = ml_result.to_scoring_breakdown_entry()
            scoring_breakdown = json.dumps(breakdown_dict)

        llm_label = None
        llm_confidence = None
        llm_rationale = None
        llm_action_hint = None
        llm_needs_review = False
        llm_called_at = None
        llm_present = bool(llm_result is not None and llm_result.model_available)
        if llm_present:
            llm_confidence = llm_result.llm_confidence
            # Record the LLM label regardless of whether it wins live — the trainer
            # learns from it (this is what breaks the rules→ML echo chamber).
            llm_label = llm_result.primary_label
            if llm_result.primary_label not in final_labels:
                final_labels.append(llm_result.primary_label)
            breakdown_dict["llm"] = llm_result.to_scoring_breakdown_entry()
            scoring_breakdown = json.dumps(breakdown_dict)

        # --- Single source of truth for label precedence ---
        primary_label, classifier_source, pipeline_used = resolve_label_precedence(
            base_label=score.primary_label,
            ml_confident=ml_confident,
            llm_present=llm_present,
            llm_label=llm_label,
            llm_confidence=llm_confidence,
            llm_override_floor=self.llm_confidence_override,
            llm_fallback_floor=self.LLM_FALLBACK_FLOOR,
            routing_source=routing_result.source if routing_result is not None else None,
            routing_label=routing_result.label if routing_result is not None else None,
        )

        # Router 'llm' (OpenAI-router) path carries extra metadata — side-effects only;
        # the label decision above already accounts for it.
        if (routing_result is not None
                and routing_result.source == "llm"
                and routing_result.llm_prediction is not None):
            llm_pred = routing_result.llm_prediction
            llm_label = llm_pred.label
            llm_confidence = llm_pred.confidence
            llm_rationale = llm_pred.rationale
            llm_action_hint = llm_pred.action_hint
            llm_needs_review = llm_pred.needs_review
            from datetime import datetime, timezone
            llm_called_at = datetime.now(timezone.utc).isoformat()
            if llm_pred.label not in final_labels:
                final_labels.append(llm_pred.label)
            LOG.debug(
                "LLM (router) result: label=%s confidence=%.4f needs_review=%s",
                llm_label, llm_confidence, llm_needs_review,
            )

        prediction = Prediction(
            email_gmail_id=email.gmail_id,
            account=getattr(email, "account", None),
            model=pipeline_used,
            labels=final_labels,
            priority_score=priority_score,
            primary_label=primary_label,
            score=priority_score,
            confidence=0.85 if pipeline_used == "rules" else ml_confidence or llm_confidence or 0.85,
            pipeline_used=pipeline_used,
            action_suggested=suggested_action,
            rule_matches=rule_names,
            scoring_breakdown=scoring_breakdown,
            ml_confidence=ml_confidence,
            llm_confidence=llm_confidence,
            llm_label=llm_label,
            llm_rationale=llm_rationale,
            llm_action_hint=llm_action_hint,
            llm_needs_review=llm_needs_review,
            classifier_source=classifier_source,
            llm_called_at=llm_called_at,
        )
        return prediction

    def add_ml_stage(self, ml_fn) -> None:
        raise NotImplementedError("ML stage not yet implemented (Phase 4)")

    def add_llm_stage(self, llm_fn) -> None:
        raise NotImplementedError("LLM stage not yet implemented (Phase 5+)")

    def add_feedback_loop(self, feedback_processor) -> None:
        raise NotImplementedError("Feedback loop not yet implemented")
