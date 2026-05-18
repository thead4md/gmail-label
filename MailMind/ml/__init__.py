"""ML module for MailMind Pass 4.

Provides lightweight scikit-learn classification for email label prediction.
All ML operations are local, deterministic, and additive to the rules pipeline.

This module contains:
- features.py:  Feature extraction from Email/Prediction models
- model.py:     ML model wrapper (TF-IDF + LogisticRegression)
- train.py:     Training orchestration from historical DB data
- inference.py: Inference orchestration for real-time predictions
"""

__all__ = ["features", "model", "train", "inference"]

from mailmind.ml.features import extract_features, FeatureVector
from mailmind.ml.model import MLClassifier, ModelMetadata
from mailmind.ml.train import train_model_from_db, train_model_from_data
from mailmind.ml.inference import predict_label, MLResult
