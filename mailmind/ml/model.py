"""ML model wrapper for MailMind classification.

Provides a scikit-learn baseline classifier using TF-IDF vectorization
and LogisticRegression. This is intentionally simple and interpretable:

1. TF-IDF vectorizer converts text features (subject, snippet, sender) into
   numerical vectors.
2. LogisticRegression provides a multi-class classifier with probability
   estimates usable as ml_confidence.
3. The model handles the case where no model is available (returns None).

The design is modular: swap in a different classifier by subclassing or
replacing the _build_classifier() method.

Model persistence uses joblib for sklearn compatibility.
"""
from __future__ import annotations

import logging
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.utils.validation import check_is_fitted

LOG = logging.getLogger(__name__)

# Default model save path
DEFAULT_MODEL_DIR = Path.home() / ".mailmind" / "models"
DEFAULT_MODEL_NAME = "pass4_baseline.joblib"


@dataclass
class ModelMetadata:
    """Metadata about a trained model for auditability."""
    version: str = "1.0.0"
    pipeline_used: str = "ml"
    class_names: List[str] = field(default_factory=list)
    num_samples: int = 0
    features_used: List[str] = field(default_factory=lambda: ["subject", "snippet", "sender"])
    accuracy: Optional[float] = None
    trained_at: Optional[str] = None
    sklearn_version: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "pipeline_used": self.pipeline_used,
            "class_names": self.class_names,
            "num_samples": self.num_samples,
            "features_used": self.features_used,
            "accuracy": self.accuracy,
            "trained_at": self.trained_at,
            "sklearn_version": self.sklearn_version,
        }


class MLClassifier:
    """Wrapper around scikit-learn text classifier.

    Provides a clean interface for:
    - Training on (text_corpus, label) pairs
    - Predicting labels with confidence
    - Saving/loading models to disk
    - Falling back gracefully when no model exists
    """

    def __init__(self, model_dir: Optional[Path] = None):
        """Initialize MLClassifier.

        Args:
            model_dir: Directory to store/load model files. Defaults to ~/.mailmind/models/.
        """
        self.model_dir = Path(model_dir or DEFAULT_MODEL_DIR)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self._pipeline: Optional[Pipeline] = None
        self._metadata: Optional[ModelMetadata] = None
        self._is_fitted = False

    def _build_pipeline(self) -> Pipeline:
        """Build the scikit-learn pipeline.

        Returns a Pipeline with:
        - TfidfVectorizer: converts text to TF-IDF features
        - LogisticRegression: multi-class classifier with probability support

        The pipeline is intentionally simple and interpretable.
        Swap this method to use a different classifier.
        """
        return Pipeline([
            ("tfidf", TfidfVectorizer(
                max_features=5000,
                stop_words="english",
                ngram_range=(1, 2),  # unigrams and bigrams
                max_df=0.85,  # ignore very common terms
                min_df=2,  # ignore very rare terms
            )),
            ("clf", LogisticRegression(
                solver="lbfgs",
                max_iter=1000,
                random_state=42,
                C=1.0,  # moderate regularization
            )),
        ])

    @property
    def is_fitted(self) -> bool:
        """Check if a model is loaded and fitted."""
        return self._is_fitted and self._pipeline is not None

    @property
    def metadata(self) -> Optional[ModelMetadata]:
        """Get model metadata if available."""
        return self._metadata

    def train(
        self,
        corpus: List[str],
        labels: List[str],
        metadata: Optional[ModelMetadata] = None,
    ) -> "MLClassifier":
        """Train the classifier on text corpus and labels.

        Args:
            corpus: List of text documents (one per sample).
            labels: Corresponding label strings.
            metadata: Optional ModelMetadata to attach.

        Returns:
            self (fitted).
        """
        if not corpus or not labels:
            raise ValueError("Training requires non-empty corpus and labels")
        if len(corpus) != len(labels):
            raise ValueError(f"Corpus length ({len(corpus)}) != labels length ({len(labels)})")

        self._pipeline = self._build_pipeline()
        self._pipeline.fit(corpus, labels)
        self._is_fitted = True

        # Build metadata
        class_names = sorted(set(labels))
        self._metadata = metadata or ModelMetadata(
            class_names=class_names,
            num_samples=len(corpus),
        )
        if not self._metadata.class_names:
            self._metadata.class_names = class_names
        if not self._metadata.num_samples:
            self._metadata.num_samples = len(corpus)

        LOG.info(
            f"Trained ML model on {len(corpus)} samples, "
            f"classes: {class_names}"
        )
        return self

    def predict(self, corpus: List[str]) -> List[Tuple[str, float]]:
        """Predict labels with confidence for one or more documents.

        Args:
            corpus: List of text documents.

        Returns:
            List of (label, confidence) tuples, one per input document.
            Confidence is the probability of the predicted class (0-1).
        """
        if not self.is_fitted:
            raise ValueError("Model is not fitted. Call train() or load() first.")

        if not corpus:
            return []

        # Predict class labels
        labels = self._pipeline.predict(corpus)  # type: ignore
        # Predict probabilities (returns array of shape [n_samples, n_classes])
        proba = self._pipeline.predict_proba(corpus)  # type: ignore

        results: List[Tuple[str, float]] = []
        for i, label in enumerate(labels):
            # Get the probability for the predicted class
            class_index = list(self._pipeline.classes_).index(label)  # type: ignore
            confidence = float(proba[i][class_index])
            results.append((label, confidence))

        return results

    def predict_single(self, text: str) -> Tuple[Optional[str], float]:
        """Predict a single document.

        Args:
            text: Text document to classify.

        Returns:
            (label, confidence) tuple. If model not fitted, returns (None, 0.0).
        """
        if not self.is_fitted:
            return None, 0.0
        try:
            results = self.predict([text])
            if results:
                return results[0]
            return None, 0.0
        except Exception as e:
            LOG.warning(f"ML prediction failed: {e}")
            return None, 0.0

    def predict_label_proba(self, corpus: List[str]) -> List[Dict[str, float]]:
        """Get full probability distribution across all classes.

        Args:
            corpus: List of text documents.

        Returns:
            List of dicts mapping class_name -> probability, one per input.
        """
        if not self.is_fitted:
            return [{} for _ in corpus]

        proba = self._pipeline.predict_proba(corpus)  # type: ignore
        class_names = list(self._pipeline.classes_)  # type: ignore

        results: List[Dict[str, float]] = []
        for row in proba:
            results.append(dict(zip(class_names, (float(v) for v in row))))
        return results

    def get_model_path(self, name: str = DEFAULT_MODEL_NAME) -> Path:
        """Get full path for a model file."""
        return self.model_dir / name

    def save(self, name: str = DEFAULT_MODEL_NAME) -> Path:
        """Save the trained model to disk using joblib.

        Args:
            name: Model filename.

        Returns:
            Path to saved model file.
        """
        if not self.is_fitted:
            raise ValueError("Cannot save unfitted model. Call train() first.")

        import joblib

        model_path = self.get_model_path(name)
        # Save pipeline and metadata
        save_data = {
            "pipeline": self._pipeline,
            "metadata": self._metadata.to_dict() if self._metadata else {},
        }
        joblib.dump(save_data, str(model_path))
        LOG.info(f"Model saved to {model_path}")
        return model_path

    def load(self, name: str = DEFAULT_MODEL_NAME) -> bool:
        """Load a trained model from disk.

        Args:
            name: Model filename.

        Returns:
            True if model loaded successfully, False otherwise.
        """
        model_path = self.get_model_path(name)
        if not model_path.exists():
            LOG.info(f"No model found at {model_path}")
            self._is_fitted = False
            self._pipeline = None
            return False

        try:
            import joblib

            save_data = joblib.load(str(model_path))
            self._pipeline = save_data.get("pipeline")
            meta_dict = save_data.get("metadata", {})

            if self._pipeline is not None:
                check_is_fitted(self._pipeline)
                self._is_fitted = True

            if meta_dict:
                self._metadata = ModelMetadata(**meta_dict)

            LOG.info(f"Model loaded from {model_path}")
            return self._is_fitted

        except Exception as e:
            LOG.warning(f"Failed to load model from {model_path}: {e}")
            self._is_fitted = False
            self._pipeline = None
            return False

    def delete(self, name: str = DEFAULT_MODEL_NAME) -> bool:
        """Delete a saved model file.

        Args:
            name: Model filename.

        Returns:
            True if deleted, False if not found.
        """
        model_path = self.get_model_path(name)
        if model_path.exists():
            model_path.unlink()
            LOG.info(f"Deleted model at {model_path}")
            return True
        return False
