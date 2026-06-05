"""Tests for hybrid prediction logic in _create_prediction (Pass 7).

Covers the combination of rules-based and LLM results:
- LLM high confidence overrides primary_label
- LLM low confidence keeps rules label
- LLM unavailable falls back to rules-only
- Scoring breakdown contains LLM entry
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from mailmind.storage.models import Email, Prediction
from mailmind.storage.database import Database
from mailmind.processing.rules import RulesEngine, RuleMatch
from mailmind.processing.scorer import PriorityScorer, ScoreResult
from mailmind.processing.pipeline import Pipeline


def _make_email(
    gmail_id: str = "hybrid_test_001",
) -> Email:
    return Email(
        gmail_id=gmail_id,
        sender="test@example.com",
        subject="Test email",
        snippet="",
        body_text="This is a test.",
        recipients=["me@example.com"],
        date_ts=int(datetime.now(timezone.utc).timestamp()),
        labels=[],
        parsed=True,
    )


def _make_score(primary_label: str = "NOTIFICATION", total_score: int = 50) -> ScoreResult:
    return ScoreResult(
        total_score=total_score,
        base_score=30,
        rule_contribution=10,
        direct_mention_bonus=5,
        recency_bonus=5,
        sender_trust=0,
        primary_label=primary_label,
    )


class TestHybridPredictionLogic:
    """Tests for hybrid prediction logic in _create_prediction."""

    def test_hybrid_label_override_high_confidence(self):
        """LLM confidence >= 0.90 overrides primary_label."""
        from mailmind.llm.deepseek import LLMResult

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = _make_email()
        score = _make_score(primary_label="NOTIFICATION", total_score=50)

        llm_result = LLMResult(
            primary_label="PERSONAL",
            llm_confidence=0.95,
            reasoning="Personal email from friend.",
            model_available=True,
        )

        # We call _create_prediction directly to isolate the merge logic
        prediction = pipeline._create_prediction(
            email, score, ["NOTIFICATION"], [],
            suggested_action=None,
            llm_result=llm_result,
        )

        assert prediction.primary_label == "PERSONAL", (
            f"Expected PERSONAL but got {prediction.primary_label}"
        )
        assert prediction.pipeline_used == "hybrid"
        assert prediction.llm_confidence == 0.95

    def test_fallback_router_does_not_clobber_confident_llm_label(self):
        """A router 'fallback' must not discard a confident DeepSeek label.

        Regression: previously routing_result.label unconditionally overwrote the
        LLM label, so Tier 3 never carried weight when rules/ML were unsure.
        """
        from mailmind.llm.deepseek import LLMResult
        from mailmind.ml.classifier_router import RoutingResult

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = _make_email()
        score = _make_score(primary_label="NOTIFICATION", total_score=40)

        llm_result = LLMResult(
            primary_label="FINANCE",
            llm_confidence=0.93,
            reasoning="Invoice from vendor.",
            model_available=True,
        )
        # Router could only produce a weak fallback guess.
        fallback = RoutingResult(source="fallback", label="NOTIFICATION", confidence=0.2)

        prediction = pipeline._create_prediction(
            email, score, ["NOTIFICATION"], [],
            suggested_action=None,
            llm_result=llm_result,
            routing_result=fallback,
        )

        assert prediction.primary_label == "FINANCE"        # DeepSeek wins, not fallback
        assert prediction.classifier_source == "llm"
        assert prediction.pipeline_used == "hybrid"
        assert prediction.llm_confidence == 0.93

    def test_fallback_router_used_when_llm_not_confident(self):
        """When DeepSeek is NOT confident, the router fallback label still applies."""
        from mailmind.llm.deepseek import LLMResult
        from mailmind.ml.classifier_router import RoutingResult

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = _make_email()
        score = _make_score(primary_label="NOTIFICATION", total_score=40)

        llm_result = LLMResult(
            primary_label="FINANCE", llm_confidence=0.50,   # below 0.90 override
            reasoning="unsure", model_available=True,
        )
        fallback = RoutingResult(source="fallback", label="NEWSLETTER", confidence=0.2)

        prediction = pipeline._create_prediction(
            email, score, ["NOTIFICATION"], [],
            suggested_action=None, llm_result=llm_result, routing_result=fallback,
        )

        assert prediction.primary_label == "NEWSLETTER"     # fallback applies
        assert prediction.classifier_source == "fallback"

    def test_hybrid_no_override_low_confidence(self):
        """LLM confidence < 0.90 keeps rules primary_label."""
        from mailmind.llm.deepseek import LLMResult

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = _make_email()
        score = _make_score(primary_label="NOTIFICATION", total_score=50)

        llm_result = LLMResult(
            primary_label="NEWSLETTER",
            llm_confidence=0.75,  # Below 0.90 override threshold
            reasoning="Maybe a newsletter.",
            model_available=True,
        )

        prediction = pipeline._create_prediction(
            email, score, ["NOTIFICATION"], [],
            suggested_action=None,
            llm_result=llm_result,
        )

        # Rules label should be kept
        assert prediction.primary_label == "NOTIFICATION"
        # Pipeline used should still be hybrid (LLM ran but didn't override)
        assert prediction.pipeline_used == "hybrid"
        assert prediction.llm_confidence == 0.75

    def test_rules_only_when_llm_unavailable(self):
        """LLM with model_available=False -> pipeline_used == 'rules'."""
        from mailmind.llm.deepseek import LLMResult

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = _make_email()
        score = _make_score(primary_label="FINANCE", total_score=50)

        llm_result = LLMResult(
            primary_label="NOTIFICATION",
            llm_confidence=0.0,
            reasoning="",
            model_available=False,
        )

        prediction = pipeline._create_prediction(
            email, score, ["FINANCE"], [],
            suggested_action=None,
            llm_result=llm_result,
        )

        assert prediction.pipeline_used == "rules"
        assert prediction.primary_label == "FINANCE"
        assert prediction.llm_confidence is None

    def test_rules_only_when_llm_is_none(self):
        """No LLM result at all -> pipeline_used == 'rules'."""
        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = _make_email()
        score = _make_score(primary_label="NOTIFICATION", total_score=50)

        prediction = pipeline._create_prediction(
            email, score, ["NOTIFICATION"], [],
            suggested_action=None,
            llm_result=None,
        )

        assert prediction.pipeline_used == "rules"
        assert prediction.llm_confidence is None
        assert prediction.primary_label == "NOTIFICATION"

    def test_scoring_breakdown_includes_llm_entry(self):
        """Verify breakdown JSON has 'llm' key with label, confidence, reasoning."""
        from mailmind.llm.deepseek import LLMResult

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = _make_email()
        score = _make_score(primary_label="CALENDAR", total_score=50)

        llm_result = LLMResult(
            primary_label="CALENDAR",
            llm_confidence=0.88,
            reasoning="Meeting-related email.",
            model_available=True,
        )

        prediction = pipeline._create_prediction(
            email, score, ["CALENDAR"], [],
            suggested_action=None,
            llm_result=llm_result,
        )

        breakdown = json.loads(prediction.scoring_breakdown)
        assert "llm" in breakdown
        llm_entry = breakdown["llm"]
        assert llm_entry["label"] == "CALENDAR"
        assert llm_entry["confidence"] == 0.88
        assert llm_entry["reasoning"] == "Meeting-related email."
