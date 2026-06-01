"""Integration tests for the processing pipeline with ML (Pass 4+).

Covers:
- Hybrid pipeline with a fitted ML model (in-memory verification)
- Population of pipeline_used, ml_confidence, rule_matches, scoring_breakdown
- Fallback to rules-only when ML is disabled, model file missing, or unfitted
- Threshold configurable via Pipeline.ML_CONFIDENCE_THRESHOLD

Note: Actual DB persistence tests for extended columns (primary_label, pipeline_used,
ml_confidence, rule_matches, scoring_breakdown) require schema migration 0007+.
These tests verify field correctness at the model and function level.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from mailmind.storage.models import Email
from mailmind.ml.model import MLClassifier
from mailmind.ml.inference import MLResult


@pytest.fixture
def trained_ml_classifier():
    """Train a tiny classifier usable for integration tests."""
    corpus = [
        "meeting project update tomorrow",
        "invoice payment due now",
        "unsubscribe from newsletter",
        "friendly project check in",
    ]
    labels = ["CALENDAR", "FINANCE", "NEWSLETTER", "WORK"]
    classifier = MLClassifier()
    classifier.train(corpus, labels)
    return classifier


def make_email(gmail_id: str = "test001", sender: str = "alice@example.com",
               subject: str = "Test Email", snippet: str = "Just a test",
               body_text: str = "This is a test email body") -> Email:
    return Email(
        gmail_id=gmail_id,
        sender=sender,
        subject=subject,
        snippet=snippet,
        body_text=body_text,
        recipients=["me@example.com"],
        date_ts=int(datetime.now(timezone.utc).timestamp()),
        labels=[],
        parsed=True,
    )


class TestHybridPipeline:
    """Tests for pipeline's _create_prediction with ML integration."""

    def test_hybrid_prediction_ml_fields(self, trained_ml_classifier):
        """_create_prediction with ML result populates ml fields correctly."""
        from mailmind.processing.pipeline import Pipeline
        from mailmind.storage.database import Database
        from mailmind.processing.rules import RulesEngine
        from mailmind.processing.scorer import PriorityScorer

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = make_email(gmail_id="hybrid_test_001")

        # Simulate ML inference
        corpus = f"{email.subject} {email.snippet}"
        ml_label, ml_confidence = trained_ml_classifier.predict_single(corpus)
        ml_result = MLResult(
            primary_label=ml_label,
            ml_confidence=ml_confidence,
            label_probabilities={},
            pipeline_used="ml",
            model_available=True,
        )

        score = pipeline.scorer.compute_score(email, [])
        prediction = pipeline._create_prediction(
            email, score, [], [], ml_result=ml_result
        )

        # pipeline_used depends on confidence vs threshold
        if ml_confidence is not None and ml_confidence >= Pipeline.ML_CONFIDENCE_THRESHOLD:
            assert prediction.pipeline_used == "hybrid"
            assert prediction.ml_confidence == ml_confidence
        else:
            assert prediction.pipeline_used == "rules"

        # rule_matches should be empty list
        assert prediction.rule_matches == []

        # scoring_breakdown should be valid JSON with ML entry when ml_result present
        breakdown = json.loads(prediction.scoring_breakdown)
        assert "ml" in breakdown
        assert "ml_primary_label" in breakdown["ml"]

    def test_rule_matches_in_prediction(self):
        """rule_matches list is correctly set in Prediction model."""
        from mailmind.processing.pipeline import Pipeline
        from mailmind.storage.database import Database
        from mailmind.processing.rules import RulesEngine
        from mailmind.processing.scorer import PriorityScorer
        from mailmind.processing.rules import RuleMatch

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = make_email(gmail_id="rule_match_test")
        score = pipeline.scorer.compute_score(email, [])
        matches = [
            RuleMatch(rule_name="sender_trusted", matched=True, labels=["WORK"]),
            RuleMatch(rule_name="keyword_meeting", matched=True, labels=["CALENDAR"]),
        ]
        prediction = pipeline._create_prediction(email, score, ["WORK", "CALENDAR"], matches)

        assert prediction.rule_matches == ["sender_trusted", "keyword_meeting"]
        # Verify format contract
        stored_joined = ",".join(prediction.rule_matches) if prediction.rule_matches else None
        assert stored_joined == "sender_trusted,keyword_meeting"
        assert stored_joined.split(",") == prediction.rule_matches

    def test_process_sets_prediction_id(self):
        """process() populates prediction.id from save_prediction (no round-trip)."""
        from mailmind.processing.pipeline import Pipeline
        from mailmind.storage.database import Database
        from mailmind.processing.rules import RulesEngine
        from mailmind.processing.scorer import PriorityScorer

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = make_email(gmail_id="id_set_test")
        db.insert_email(email)
        prediction = pipeline.process(email)

        assert prediction.id is not None
        rows = db.get_predictions_for_email("id_set_test")
        assert len(rows) == 1
        assert prediction.id == rows[0]["id"]


class TestFallbackBehavior:
    """Tests for pipeline fallback when ML is unavailable."""

    def test_fallback_no_ml_result(self):
        """No ML result passed -> pipeline_used == 'rules'."""
        from mailmind.processing.pipeline import Pipeline
        from mailmind.storage.database import Database
        from mailmind.processing.rules import RulesEngine
        from mailmind.processing.scorer import PriorityScorer

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = make_email(gmail_id="fallback_no_ml")
        score = pipeline.scorer.compute_score(email, [])
        prediction = pipeline._create_prediction(email, score, [], [])
        assert prediction.pipeline_used == "rules"
        assert prediction.ml_confidence is None

    def test_fallback_model_unavailable_mlresult(self):
        """MLResult with model_available=False -> pipeline_used == 'rules'."""
        from mailmind.processing.pipeline import Pipeline
        from mailmind.storage.database import Database
        from mailmind.processing.rules import RulesEngine
        from mailmind.processing.scorer import PriorityScorer

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = make_email(gmail_id="fallback_unavailable")
        score = pipeline.scorer.compute_score(email, [])
        ml_result = MLResult(
            primary_label=None,
            ml_confidence=None,
            pipeline_used="rules",
            model_available=False,
        )
        prediction = pipeline._create_prediction(
            email, score, [], [], ml_result=ml_result
        )
        assert prediction.pipeline_used == "rules"
        assert prediction.ml_confidence is None

    def test_fallback_low_confidence(self):
        """ML confidence below threshold -> pipeline_used == 'rules'."""
        from mailmind.processing.pipeline import Pipeline
        from mailmind.storage.database import Database
        from mailmind.processing.rules import RulesEngine
        from mailmind.processing.scorer import PriorityScorer

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = make_email(gmail_id="fallback_low_conf")
        score = pipeline.scorer.compute_score(email, [])
        ml_result = MLResult(
            primary_label="NEWSLETTER",
            ml_confidence=0.05,  # Below default threshold of 0.3
            pipeline_used="ml",
            model_available=True,
        )
        prediction = pipeline._create_prediction(
            email, score, [], [], ml_result=ml_result
        )
        assert prediction.pipeline_used == "rules"
        assert prediction.ml_confidence == 0.05

    def test_hybrid_threshold_boundary(self):
        """ML confidence exactly at threshold -> pipeline_used == 'hybrid'."""
        from mailmind.processing.pipeline import Pipeline
        from mailmind.storage.database import Database
        from mailmind.processing.rules import RulesEngine
        from mailmind.processing.scorer import PriorityScorer

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = make_email(gmail_id="hybrid_boundary")
        score = pipeline.scorer.compute_score(email, [])
        ml_result = MLResult(
            primary_label="WORK",
            ml_confidence=0.3,  # Exactly threshold
            pipeline_used="ml",
            model_available=True,
        )
        prediction = pipeline._create_prediction(
            email, score, [], [], ml_result=ml_result
        )
        assert prediction.pipeline_used == "hybrid"
        assert prediction.ml_confidence == 0.3
        assert "WORK" in prediction.labels  # ML label added

    def test_hybrid_threshold_configurable(self):
        """The threshold can be overridden via Pipeline.ML_CONFIDENCE_THRESHOLD."""
        from mailmind.processing.pipeline import Pipeline
        from mailmind.storage.database import Database
        from mailmind.processing.rules import RulesEngine
        from mailmind.processing.scorer import PriorityScorer

        assert Pipeline.ML_CONFIDENCE_THRESHOLD == 0.3  # default unchanged

        original_threshold = Pipeline.ML_CONFIDENCE_THRESHOLD
        try:
            Pipeline.ML_CONFIDENCE_THRESHOLD = 0.5
            db = Database(":memory:")
            pipeline = Pipeline(db, RulesEngine(), PriorityScorer())
            assert pipeline.ML_CONFIDENCE_THRESHOLD == 0.5

            email = make_email(gmail_id="hybrid_configurable")
            score = pipeline.scorer.compute_score(email, [])
            ml_result = MLResult(
                primary_label="WORK",
                ml_confidence=0.4,  # Below 0.5, would be hybrid with default 0.3
                pipeline_used="ml",
                model_available=True,
            )
            prediction = pipeline._create_prediction(
                email, score, [], [], ml_result=ml_result
            )
            assert prediction.pipeline_used == "rules"  # Below modified threshold
        finally:
            Pipeline.ML_CONFIDENCE_THRESHOLD = original_threshold

    def test_scoring_breakdown_ml_entry_format(self):
        """Scoring breakdown contains 'ml' entry with expected keys when ML used."""
        from mailmind.processing.pipeline import Pipeline
        from mailmind.storage.database import Database
        from mailmind.processing.rules import RulesEngine
        from mailmind.processing.scorer import PriorityScorer

        db = Database(":memory:")
        pipeline = Pipeline(db, RulesEngine(), PriorityScorer())

        email = make_email(gmail_id="breakdown_test")
        score = pipeline.scorer.compute_score(email, [])
        ml_result = MLResult(
            primary_label="FINANCE",
            ml_confidence=0.75,
            label_probabilities={"FINANCE": 0.75, "WORK": 0.25},
            pipeline_used="ml",
            model_available=True,
        )
        prediction = pipeline._create_prediction(
            email, score, [], [], ml_result=ml_result
        )
        breakdown = json.loads(prediction.scoring_breakdown)
        assert "ml" in breakdown
        ml_entry = breakdown["ml"]
        assert "ml_primary_label" in ml_entry
        assert ml_entry["ml_primary_label"] == "FINANCE"
        assert "ml_confidence" in ml_entry
        assert ml_entry["ml_confidence"] == 0.75
        assert "ml_model_available" in ml_entry
        assert ml_entry["ml_model_available"] is True
