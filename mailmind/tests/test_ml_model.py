"""Tests for ML model wrapper (MLClassifier).

Tests cover:
- Training with tiny fixtures
- Prediction and confidence
- Model save/load round-trip
- Fallback when no model exists
- Error handling
"""
from __future__ import annotations

import pytest
import tempfile
from pathlib import Path

from mailmind.ml.model import MLClassifier, ModelMetadata


# Tiny training fixtures
SIMPLE_CORPUS = [
    "Meeting tomorrow at 3pm please confirm",
    "Your invoice for last month is ready",
    "Unsubscribe from our weekly newsletter",
    "Hey, just checking in on the project",
    "Your account statement is available",
    "Team standup meeting invite for Monday",
    "Special offer just for you! 50% off",
    "Payment confirmation for your order",
]

SIMPLE_LABELS = [
    "CALENDAR",
    "FINANCE",
    "NEWSLETTER",
    "WORK",
    "FINANCE",
    "CALENDAR",
    "NEWSLETTER",
    "FINANCE",
]


class TestMLClassifier:
    """Test MLClassifier core functionality."""

    def test_train_and_predict(self):
        """Test basic training and prediction."""
        classifier = MLClassifier()
        classifier.train(SIMPLE_CORPUS, SIMPLE_LABELS)
        assert classifier.is_fitted is True

        # Predict on training data
        results = classifier.predict(SIMPLE_CORPUS)
        assert len(results) == len(SIMPLE_CORPUS)
        for label, confidence in results:
            assert isinstance(label, str)
            assert 0.0 <= confidence <= 1.0

    def test_predict_single(self):
        """Test single prediction."""
        classifier = MLClassifier()
        classifier.train(SIMPLE_CORPUS, SIMPLE_LABELS)

        label, confidence = classifier.predict_single("Meeting invite for tomorrow")
        assert label is not None
        assert 0.0 <= confidence <= 1.0

    def test_predict_empty_corpus(self):
        """Test predict with empty list."""
        classifier = MLClassifier()
        classifier.train(SIMPLE_CORPUS, SIMPLE_LABELS)
        assert classifier.predict([]) == []

    def test_predict_unfitted(self):
        """Test predict raises when model not fitted."""
        classifier = MLClassifier()
        with pytest.raises(ValueError, match="not fitted"):
            classifier.predict(["test"])

    def test_predict_single_unfitted(self):
        """Test predict_single returns fallback when unfitted."""
        classifier = MLClassifier()
        label, confidence = classifier.predict_single("test")
        assert label is None
        assert confidence == 0.0

    def test_is_fitted_property(self):
        """Test is_fitted property reflects state."""
        classifier = MLClassifier()
        assert classifier.is_fitted is False
        classifier.train(SIMPLE_CORPUS, SIMPLE_LABELS)
        assert classifier.is_fitted is True

    def test_metadata_after_training(self):
        """Test metadata is populated after training."""
        classifier = MLClassifier()
        meta = ModelMetadata(
            version="test",
            class_names=["CALENDAR", "FINANCE", "NEWSLETTER", "WORK"],
            num_samples=len(SIMPLE_CORPUS),
        )
        classifier.train(SIMPLE_CORPUS, SIMPLE_LABELS, metadata=meta)
        assert classifier.metadata is not None
        assert classifier.metadata.num_samples == len(SIMPLE_CORPUS)
        assert "CALENDAR" in classifier.metadata.class_names

    def test_save_and_load(self):
        """Test model save/load round-trip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            model_dir = Path(tmpdir)
            classifier = MLClassifier(model_dir=model_dir)
            classifier.train(SIMPLE_CORPUS, SIMPLE_LABELS)

            # Save
            saved_path = classifier.save("test_model.joblib")
            assert saved_path.exists()

            # Load into new classifier
            classifier2 = MLClassifier(model_dir=model_dir)
            loaded = classifier2.load("test_model.joblib")
            assert loaded is True
            assert classifier2.is_fitted is True

            # Predictions should match
            label1, conf1 = classifier.predict_single("Meeting at 3pm")
            label2, conf2 = classifier2.predict_single("Meeting at 3pm")
            assert label1 == label2

    def test_load_missing_model(self):
        """Test loading non-existent model returns False gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            classifier = MLClassifier(model_dir=Path(tmpdir))
            loaded = classifier.load("nonexistent.joblib")
            assert loaded is False
            assert classifier.is_fitted is False

    def test_save_unfitted(self):
        """Test saving unfitted model raises error."""
        classifier = MLClassifier()
        with pytest.raises(ValueError, match="unfitted"):
            classifier.save("test.joblib")

    def test_predict_label_proba(self):
        """Test full probability distribution."""
        classifier = MLClassifier()
        classifier.train(SIMPLE_CORPUS, SIMPLE_LABELS)

        probas = classifier.predict_label_proba(["Meeting at 3pm"])
        assert len(probas) == 1
        proba_dict = probas[0]
        # Should have all classes
        for cls_name in ["CALENDAR", "FINANCE", "NEWSLETTER", "WORK"]:
            assert cls_name in proba_dict
            assert 0.0 <= proba_dict[cls_name] <= 1.0
        # Probabilities should sum to ~1.0
        total = sum(proba_dict.values())
        assert abs(total - 1.0) < 0.01

    def test_delete_model(self):
        """Test model deletion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            classifier = MLClassifier(model_dir=Path(tmpdir))
            classifier.train(SIMPLE_CORPUS, SIMPLE_LABELS)
            saved_path = classifier.save("test_delete.joblib")
            assert saved_path.exists()

            deleted = classifier.delete("test_delete.joblib")
            assert deleted is True
            assert not saved_path.exists()

            # Delete non-existent
            deleted = classifier.delete("nonexistent.joblib")
            assert deleted is False

    def test_train_raises_on_empty(self):
        """Test training with empty data raises."""
        classifier = MLClassifier()
        with pytest.raises(ValueError, match="non-empty"):
            classifier.train([], [])

    def test_train_raises_on_mismatch(self):
        """Test training with mismatched lengths raises."""
        classifier = MLClassifier()
        with pytest.raises(ValueError, match="length"):
            classifier.train(["one", "two"], ["label"])


class TestModelMetadata:
    """Test ModelMetadata dataclass."""

    def test_defaults(self):
        meta = ModelMetadata()
        assert meta.version == "1.0.0"
        assert meta.pipeline_used == "ml"
        assert meta.class_names == []
        assert meta.num_samples == 0
        assert meta.accuracy is None

    def test_to_dict(self):
        meta = ModelMetadata(
            class_names=["WORK", "FINANCE"],
            num_samples=100,
            accuracy=0.85,
        )
        d = meta.to_dict()
        assert d["class_names"] == ["WORK", "FINANCE"]
        assert d["num_samples"] == 100
        assert d["accuracy"] == 0.85
        assert "trained_at" in d
