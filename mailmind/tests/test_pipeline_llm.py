"""Tests for Pipeline integration with DeepSeek LLM (Pass 7).

All LLM API calls are mocked — no real DeepSeek API calls are made.

Covers:
- LLM called when rules score is below skip threshold
- LLM called despite a high priority score when nothing classified confidently
  (priority/importance must never gate the LLM call by itself)
- LLM skipped when a router tier already classified confidently
- LLM skipped when budget is exhausted
- LLM skipped when client is None
- LLM result merged into Prediction correctly
- Fallback to rules-only on LLM failure
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from mailmind.storage.models import Email
from mailmind.storage.database import Database
from mailmind.processing.rules import RulesEngine
from mailmind.processing.scorer import PriorityScorer, ScoreResult
from mailmind.processing.pipeline import Pipeline


def _make_email(
    gmail_id: str = "llm_pipe_test_001",
    sender: str = "alice@example.com",
    subject: str = "Hey, how are you?",
    body_text: str = "Hi friend, just checking in.",
) -> Email:
    return Email(
        gmail_id=gmail_id,
        sender=sender,
        subject=subject,
        snippet="",
        body_text=body_text,
        recipients=["me@example.com"],
        date_ts=int(datetime.now(timezone.utc).timestamp()),
        labels=[],
        parsed=True,
    )


class TestLLMPipelineIntegration:
    """Tests for LLM integration in the pipeline process method."""

    def test_llm_called_when_score_below_threshold(
        self, mock_deepseek_client
    ):
        """Verify LLM is called when rules score is below skip threshold."""
        db = Database(":memory:")
        pipeline = Pipeline(
            db=db,
            rules_engine=RulesEngine(),
            scorer=PriorityScorer(),
            llm_client=mock_deepseek_client,
            llm_skip_threshold=70,
            llm_max_calls_per_run=10,
        )

        email = _make_email(gmail_id="llm_call_test_001")
        prediction = pipeline.process(email)

        # LLM should have been called
        mock_deepseek_client.classify_email.assert_called_once_with(email)
        # Pipeline should be hybrid
        assert prediction.pipeline_used == "hybrid"
        # LLM confidence should be set
        assert prediction.llm_confidence == 0.92

    def test_llm_called_despite_high_score_when_unclassified(self, mock_deepseek_client):
        """A high PRIORITY score must not skip the LLM by itself.

        total_score is importance (direct-mention + recency + ...), not
        classification confidence. This pipeline has no classifier_router, so
        nothing has actually classified the email yet — the LLM must still be
        called regardless of how important it scores. This replaces a test
        that asserted the opposite (the exact bug: gating the LLM call on
        `score.total_score < llm_skip_threshold`) — see pipeline.py's
        process(), which now gates on `ml_or_rules_handled` instead.
        """
        db = Database(":memory:")
        pipeline = Pipeline(
            db=db,
            rules_engine=RulesEngine(user_email="me@example.com"),
            scorer=PriorityScorer(user_email="me@example.com"),
            llm_client=mock_deepseek_client,
            llm_skip_threshold=70,
            llm_max_calls_per_run=10,
        )

        # Create an email that will score high on IMPORTANCE alone (direct
        # mention + recency) despite matching no confident classification rule.
        email = _make_email(
            gmail_id="llm_skip_test_001",
            sender="billing@company.com",
            subject="Your invoice #12345 - Payment Due",
            body_text="This is an invoice for your recent purchase.",
        )
        prediction = pipeline.process(email)

        # LLM SHOULD have been called — no router/rule tier confidently
        # classified this email, so its high priority score must not skip it.
        mock_deepseek_client.classify_email.assert_called_once_with(email)
        assert prediction.pipeline_used == "hybrid"
        assert prediction.llm_confidence == 0.92

    def test_llm_skipped_when_router_already_handled_confidently(self):
        """LLM must be skipped when a router tier already classified confidently.

        This is the actual intent the old (buggy) test was reaching for: don't
        waste a paid LLM call — but the real gate must be "already classified
        with confidence" (ml_or_rules_handled), not "importance score is high".
        """
        from unittest.mock import MagicMock
        from mailmind.ml.classifier_router import RoutingResult

        db = Database(":memory:")
        mock_llm = MagicMock()
        mock_router = MagicMock()
        mock_router.route.return_value = RoutingResult(
            source="rules", label="FINANCE", confidence=0.95,
        )
        pipeline = Pipeline(
            db=db,
            rules_engine=RulesEngine(user_email="me@example.com"),
            scorer=PriorityScorer(user_email="me@example.com"),
            llm_client=mock_llm,
            llm_skip_threshold=70,
            llm_max_calls_per_run=10,
            classifier_router=mock_router,
        )

        email = _make_email(
            gmail_id="llm_skip_test_002",
            sender="billing@company.com",
            subject="Your invoice #12345 - Payment Due",
            body_text="This is an invoice for your recent purchase.",
        )
        prediction = pipeline.process(email)

        mock_llm.classify_email.assert_not_called()
        assert prediction.primary_label == "FINANCE"

    def test_llm_skipped_when_budget_exhausted(self, mock_deepseek_client):
        """Verify LLM stops being called after budget is exhausted."""
        db = Database(":memory:")
        pipeline = Pipeline(
            db=db,
            rules_engine=RulesEngine(),
            scorer=PriorityScorer(),
            llm_client=mock_deepseek_client,
            llm_skip_threshold=70,
            llm_max_calls_per_run=2,  # Small budget for testing
        )

        # Process 3 low-scoring emails
        for i in range(3):
            email = _make_email(
                gmail_id=f"llm_budget_test_{i:03d}",
                subject=f"Low priority notification {i}",
                body_text="Just a regular notification.",
            )
            pipeline.process(email)

        # LLM should have been called only twice (budget exhausted)
        assert mock_deepseek_client.classify_email.call_count == 2

    def test_llm_skipped_when_client_is_none(self):
        """Verify pipeline works fine when LLM client is None."""
        db = Database(":memory:")
        pipeline = Pipeline(
            db=db,
            rules_engine=RulesEngine(),
            scorer=PriorityScorer(),
            llm_client=None,  # No LLM client
        )

        email = _make_email(gmail_id="llm_none_test_001")
        prediction = pipeline.process(email)

        # Should work without error
        assert prediction.pipeline_used == "rules"
        assert prediction.llm_confidence is None

    def test_llm_label_override_high_confidence(self, mock_deepseek_client):
        """Test LLM overrides primary_label when confidence is high.

        Uses the default 0.90 override threshold.
        """
        db = Database(":memory:")
        pipeline = Pipeline(
            db=db,
            rules_engine=RulesEngine(),
            scorer=PriorityScorer(),
            llm_client=mock_deepseek_client,
            llm_skip_threshold=70,
        )

        # mock_deepseek_client returns PERSONAL with 0.92 confidence
        email = _make_email(gmail_id="llm_override_001")
        prediction = pipeline.process(email)

        # LLM confidence (0.92) >= override threshold (0.90) -> override
        assert prediction.primary_label == "PERSONAL"
        assert prediction.pipeline_used == "hybrid"
        assert prediction.llm_confidence == 0.92

    def test_llm_no_override_low_confidence(self):
        """Test LLM does NOT override primary_label when confidence is low."""
        from mailmind.llm.deepseek import LLMResult

        db = Database(":memory:")
        # Create a mock client with low confidence
        low_conf_client = MagicMock()
        low_conf_client.classify_email.return_value = LLMResult(
            primary_label="NEWSLETTER",
            llm_confidence=0.45,  # Below 0.90 override threshold
            reasoning="Low confidence newsletter guess.",
            model_available=True,
        )

        pipeline = Pipeline(
            db=db,
            rules_engine=RulesEngine(),
            scorer=PriorityScorer(),
            llm_client=low_conf_client,
            llm_skip_threshold=70,
        )

        email = _make_email(gmail_id="llm_no_override_001")
        prediction = pipeline.process(email)

        # LLM ran but confidence too low for override
        assert prediction.pipeline_used == "hybrid"
        assert prediction.llm_confidence == 0.45
        # primary_label stays as rules result (likely NOTIFICATION)
        assert prediction.primary_label is not None

    def test_scoring_breakdown_includes_llm_entry(self, mock_deepseek_client):
        """Verify scoring_breakdown JSON contains llm entry."""
        db = Database(":memory:")
        pipeline = Pipeline(
            db=db,
            rules_engine=RulesEngine(),
            scorer=PriorityScorer(),
            llm_client=mock_deepseek_client,
            llm_skip_threshold=70,
        )

        email = _make_email(gmail_id="breakdown_llm_001")
        prediction = pipeline.process(email)

        breakdown = json.loads(prediction.scoring_breakdown)
        assert "llm" in breakdown
        llm_entry = breakdown["llm"]
        assert llm_entry["label"] == "PERSONAL"
        assert llm_entry["confidence"] == 0.92
        assert "reasoning" in llm_entry

    def test_llm_failure_falls_back_to_rules(self):
        """Test that LLM failure gracefully falls back to rules-only."""
        from mailmind.llm.deepseek import LLMResult

        db = Database(":memory:")
        # Create a mock client that simulates failure
        failed_client = MagicMock()
        failed_client.classify_email.return_value = LLMResult(
            primary_label="NOTIFICATION",
            llm_confidence=0.0,
            reasoning="",
            model_available=False,
        )

        pipeline = Pipeline(
            db=db,
            rules_engine=RulesEngine(),
            scorer=PriorityScorer(),
            llm_client=failed_client,
            llm_skip_threshold=70,
        )

        email = _make_email(gmail_id="llm_fail_001")
        prediction = pipeline.process(email)

        # Should fall back to rules-only
        assert prediction.pipeline_used == "rules"
        assert prediction.llm_confidence is None
