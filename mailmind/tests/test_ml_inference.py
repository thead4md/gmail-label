"""Tests for ML inference orchestration.

Tests cover:
- Inference with trained model
- Fallback when no model exists (MLClassifier is None)
- Fallback when model not fitted
- MLResult structure and fields
- Pipeline_used correctness
- ml_confidence population
- Error handling
"""
from __future__ import annotations

from datetime import datetime, timezone

from mailmind.storage.models import Email
from mailmind.ml.model import MLClassifier
from mailmind.ml.inference import predict_label, MLResult


def _make_test_email(
    gmail_id: str = "infer_test_001",
    sender: str = "bob@example.com",
    subject: str = "Project update meeting",
    snippet: str = "Let's sync on the project",
    body_text: str = "Hi team, let's meet tomorrow to discuss progress",
) -> Email:
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


# Tiny training corpus for inference tests
TRAIN_CORPUS = [
    "Meeting tomorrow at 3pm please confirm",
    "Your invoice for last month is ready",
    "Unsubscribe from our weekly newsletter",
    "Hey, just checking in on the project",
    "Your account statement is available",
    "Team standup meeting invite for Monday",
    "Special offer just for you! 50% off",
    "Payment confirmation for your order #12345",
]

TRAIN_LABELS = [
    "CALENDAR",
    "FINANCE",
    "NEWSLETTER",
    "WORK",
    "FINANCE",
    "CALENDAR",
    "NEWSLETTER",
    "FINANCE",
]


class TestInferenceWithModel:
    """Test inference when model is available."""

    @classmethod
    def setup_class(cls):
        cls.classifier = MLClassifier()
        cls.classifier.train(TRAIN_CORPUS, TRAIN_LABELS)

    def test_predict_with_model(self):
        """Test inference returns label and confidence with model."""
        email = _make_test_email()
        result = predict_label(email, self.classifier)
        assert result.model_available is True
        assert result.primary_label is not None
        assert result.ml_confidence is not None
        assert 0.0 <= result.ml_confidence <= 1.0
        assert result.error is None

    def test_pipeline_used_with_sufficient_confidence(self):
        """Test pipeline_used is 'ml' when confidence >= 0.3."""
        email = _make_test_email()
        result = predict_label(email, self.classifier)
        if result.ml_confidence is not None and result.ml_confidence >= 0.3:
            assert result.pipeline_used == "ml"
        else:
            assert result.pipeline_used == "rules"

    def test_label_probabilities(self):
        """Test label_probabilities are populated."""
        email = _make_test_email()
        result = predict_label(email, self.classifier)
        assert isinstance(result.label_probabilities, dict)
        if result.model_available:
            assert len(result.label_probabilities) > 0

    def test_to_scoring_breakdown_entry(self):
        """Test MLResult converts to scoring breakdown dict."""
        email = _make_test_email()
        result = predict_label(email, self.classifier)
        entry = result.to_scoring_breakdown_entry()
        assert isinstance(entry, dict)
        assert "ml_primary_label" in entry
        assert "ml_confidence" in entry
        assert "ml_pipeline_used" in entry
        assert "ml_model_available" in entry

    def test_predict_newsletter_signal(self):
        """Test inference on newsletter-style email."""
        email = _make_test_email(
            subject="Your weekly newsletter",
            body_text="Click here to unsubscribe from our mailing list",
        )
        result = predict_label(email, self.classifier)
        # ML may or may not predict NEWSLETTER, but should run without error
        assert result.error is None
        if result.model_available:
            assert result.primary_label is not None


class TestInferenceFallback:
    """Test inference fallback when model is not available."""

    def test_no_classifier(self):
        """Test fallback when classifier is None."""
        email = _make_test_email()
        result = predict_label(email, None)
        assert result.model_available is False
        assert result.primary_label is None
        assert result.ml_confidence is None
        assert result.pipeline_used == "rules"

    def test_unfitted_classifier(self):
        """Test fallback when classifier exists but not fitted."""
        email = _make_test_email()
        classifier = MLClassifier()  # Not trained
        result = predict_label(email, classifier)
        assert result.model_available is False
        assert result.primary_label is None
        assert result.pipeline_used == "rules"

    def test_empty_text_corpus(self):
        """Test inference with email that has no text content."""
        email = _make_test_email(
            subject="",
            snippet="",
            body_text="",
        )
        classifier = MLClassifier()
        classifier.train(TRAIN_CORPUS, TRAIN_LABELS)
        result = predict_label(email, classifier)
        # Should not crash, return fallback
        assert result.error is not None or result.model_available is True


class TestMLResultStructure:
    """Test MLResult dataclass fields."""

    def test_default_mlresult(self):
        """Test default MLResult values."""
        result = MLResult()
        assert result.primary_label is None
        assert result.ml_confidence is None
        assert result.label_probabilities == {}
        assert result.pipeline_used == "rules"
        assert result.model_available is False
        assert result.error is None

    def test_mlresult_with_values(self):
        """Test MLResult with custom values."""
        result = MLResult(
            primary_label="WORK",
            ml_confidence=0.92,
            label_probabilities={"WORK": 0.92, "CALENDAR": 0.08},
            pipeline_used="ml",
            model_available=True,
        )
        assert result.primary_label == "WORK"
        assert result.ml_confidence == 0.92
        assert result.label_probabilities["WORK"] == 0.92
        assert result.pipeline_used == "ml"

    def test_to_scoring_breakdown_entry_default(self):
        """Test scoring breakdown entry from default MLResult."""
        result = MLResult()
        entry = result.to_scoring_breakdown_entry()
        assert entry["ml_model_available"] is False
        assert entry["ml_primary_label"] is None
