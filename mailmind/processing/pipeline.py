"""MailMind processing pipeline: orchestrates rules, scoring, and actions.

This is the core MVP deterministic pipeline:
Email -> Rules -> Score -> Prediction Persistence -> Optional Safe Action

For MVP, the pipeline uses only rules-based classification.
ML/LLM stages are stubbed as TODO extension points for future phases.
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from ..storage.models import Email, Prediction
from ..storage.database import Database
from .rules import RulesEngine
from .scorer import PriorityScorer, ScoreResult
from ..actions.safety import SafetyPolicy

if TYPE_CHECKING:
    from ..ml.inference import MLResult
    from ..actions.executor import ActionExecutor

LOG = logging.getLogger(__name__)


class Pipeline:
    """Deterministic MVP processing pipeline.

    Orchestrates:
    1. Rule matching
    2. Priority scoring
    3. Prediction persistence
    4. Optional safe action execution
    """

    # Configurable ML confidence threshold for hybrid pipeline.
    # When ml_confidence >= ML_CONFIDENCE_THRESHOLD, the pipeline_used is "hybrid".
    ML_CONFIDENCE_THRESHOLD = 0.3

    # Default LLM confidence threshold for overriding primary_label.
    LLM_CONFIDENCE_OVERRIDE = 0.90

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

    def process(
        self,
        email: Email,
        auto_action: bool = False,
    ) -> Prediction:
        """Process an email through the full pipeline.

        Args:
            email: Normalized Email model.
            auto_action: If True, attempt to execute suggested action.

        Returns:
            Prediction model (persisted to database).
        """
        LOG.info(f"Processing email {email.gmail_id} from {email.sender}")

        # 1. Run rules
        rule_matches = self.rules_engine.evaluate(email)
        matched_rules = [m for m in rule_matches if m.matched]
        LOG.debug(f"Matched {len(matched_rules)} rules: {[m.rule_name for m in matched_rules]}")

        # 2. Compute priority score
        score = self.scorer.compute_score(email, rule_matches)
        LOG.debug(f"Score: {score.total_score} (primary_label: {score.primary_label})")
        LOG.debug(f"Breakdown:\n{score.breakdown_text}")

        # 3. Collect labels from rules and email
        final_labels = list(set(email.labels or []))
        for match in matched_rules:
            if match.labels:
                final_labels.extend(match.labels)
        final_labels = list(set(final_labels))  # Deduplicate

        # Compute suggested_action unconditionally for queue manager
        suggested_action = self.executor.suggest_action(email, score) if self.executor else None

        # 3b. Run LLM classification (Pass 7+) if:
        #   - LLM client is available
        #   - Budget not exhausted
        #   - Rules score is below skip threshold (cost control)
        llm_result = None
        if (
            self.llm_client is not None
            and self._llm_calls_this_run < self.llm_max_calls_per_run
            and score.total_score < self.llm_skip_threshold
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
        elif score.total_score >= self.llm_skip_threshold:
            LOG.debug(
                "Skipping LLM for %s: rules score %d >= threshold %d",
                email.gmail_id, score.total_score, self.llm_skip_threshold,
            )

        # 4. Create Prediction model (with optional LLM result)
        prediction = self._create_prediction(
            email, score, final_labels, matched_rules,
            suggested_action=suggested_action,
            llm_result=llm_result,
        )

        # 5. Persist prediction
        try:
            self.db.save_prediction(prediction)
            LOG.info(f"Prediction persisted for {email.gmail_id}: score={score.total_score}, label={score.primary_label}")
        except Exception as e:
            LOG.error(f"Failed to persist prediction: {e}", exc_info=True)

        # 6. Optionally execute safe action
        if auto_action and self.executor:
            suggested_action = self.executor.suggest_action(email, score)
            if suggested_action:
                LOG.debug(f"Suggested action: {suggested_action} (score: {score.total_score})")
                executed = self.executor.execute_action(email, suggested_action, score)
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
        ml_result: Optional["MLResult"] = None,  # type: ignore
        suggested_action: Optional[str] = None,
        llm_result: Optional["LLMResult"] = None,
    ) -> Prediction:
        """Create a Prediction model combining rules + optional ML/LLM results.

        Hybrid combination strategy (conservative):
        - Rules always determine the base score and primary_label.
        - ML contributes ml_confidence and optional label suggestion.
        - pipeline_used set to "hybrid" when ML contributes, "rules" otherwise.
        - ML contributions are recorded in scoring_breakdown for full auditability.

        Args:
            email: The email being processed.
            score: ScoreResult from PriorityScorer.
            labels: Final labels from rules and email.
            matched_rules: List of matched RuleMatch objects.
            ml_result: Optional MLResult from ML inference.

        Returns:
            Prediction model with all Pass 4 fields populated.
        """
        import json

        # Serialize scoring breakdown as JSON for durable storage
        breakdown_dict = score.to_dict()
        scoring_breakdown = json.dumps(breakdown_dict)

        # Collect matched rule names
        rule_names = [m.rule_name for m in matched_rules]

        # Determine pipeline mode and merge ML result
        final_labels = list(labels)
        pipeline_used = "rules"
        ml_confidence = None
        primary_label = score.primary_label
        priority_score = score.total_score

        if ml_result and ml_result.model_available and ml_result.primary_label is not None:
            ml_confidence = ml_result.ml_confidence

            if ml_confidence is not None and ml_confidence >= self.ML_CONFIDENCE_THRESHOLD:
                pipeline_used = "hybrid"

                # If ML predicts a different label, add as secondary suggestion
                if ml_result.primary_label not in final_labels:
                    final_labels.append(ml_result.primary_label)

            # Enrich breakdown with ML data
            breakdown_dict["ml"] = ml_result.to_scoring_breakdown_entry()
            scoring_breakdown = json.dumps(breakdown_dict)

        # Merge LLM result (Pass 7+)
        llm_confidence = None
        if llm_result is not None and llm_result.model_available:
            pipeline_used = "hybrid"
            llm_confidence = llm_result.llm_confidence

            # Override primary_label if LLM confidence is high enough
            if llm_confidence >= self.llm_confidence_override:
                primary_label = llm_result.primary_label
                LOG.debug(
                    "LLM override: label=%s (confidence=%.2f >= %.2f threshold)",
                    primary_label, llm_confidence, self.llm_confidence_override,
                )

            # Add LLM label to labels list if not already present
            if llm_result.primary_label not in final_labels:
                final_labels.append(llm_result.primary_label)

            # Enrich breakdown with LLM data
            breakdown_dict["llm"] = llm_result.to_scoring_breakdown_entry()
            scoring_breakdown = json.dumps(breakdown_dict)

        prediction = Prediction(
            email_gmail_id=email.gmail_id,
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
        )
        return prediction

    # --- Future extension points (Phase 4+) ---

    def add_ml_stage(self, ml_fn) -> None:
        """TODO: Add ML-based classification stage."""
        raise NotImplementedError("ML stage not yet implemented (Phase 4)")

    def add_llm_stage(self, llm_fn) -> None:
        """TODO: Add LLM-based classification/summarization stage."""
        raise NotImplementedError("LLM stage not yet implemented (Phase 5+)")

    def add_feedback_loop(self, feedback_processor) -> None:
        """TODO: Add feedback loop for online learning."""
        raise NotImplementedError("Feedback loop not yet implemented")

