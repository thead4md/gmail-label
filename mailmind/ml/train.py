"""Training orchestration for MailMind ML classifier.

Provides functions to train the ML model from historical data in the local
SQLite database and from explicit corpus/labels pairs for testing.

NOTE: This module includes warnings about data leakage — treat training with
care in production.
"""

from __future__ import annotations

import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple, Optional

from ..storage.database import Database
from ..storage.models import Email  # noqa: F401 - used for type hints
from .features import extract_features, FeatureVector, VALID_LABELS, build_model_text
from .model import MLClassifier, ModelMetadata

LOG = logging.getLogger(__name__)

# Minimum samples needed to train a useful model
MIN_TRAINING_SAMPLES = 10

# Minimum training examples a label needs to be learnable by the local model.
# Classes below this are dropped from ML training (the LLM tier still labels them
# live) — e.g. RECEIPT with ~11 examples just added noise and 0.00 F1. Rises are
# cheap as data accrues; new labels graduate into the model once they clear it.
MIN_SAMPLES_PER_CLASS = 15


def _evaluate_holdout(corpus, labels, test_size: float = 0.2, seed: int = 42):
    """Measure classifier accuracy on a stratified hold-out split.

    Returns a float in [0, 1] (rounded), or None when the data is too small /
    too imbalanced to split reliably (in which case the caller should treat the
    model as unvalidated). Trains a throwaway model on the train split only —
    the caller refits on the full corpus for the deployed model.
    """
    from collections import Counter
    counts = Counter(labels)
    if len(counts) < 2 or min(counts.values()) < 2 or len(corpus) < 10:
        return None
    try:
        from sklearn.model_selection import train_test_split
        X_tr, X_te, y_tr, y_te = train_test_split(
            corpus, labels, test_size=test_size, random_state=seed, stratify=labels,
        )
        probe = MLClassifier()
        probe.train(
            X_tr, y_tr,
            metadata=ModelMetadata(class_names=sorted(set(y_tr)), num_samples=len(X_tr)),
        )
        preds = [lbl for lbl, _conf in probe.predict(X_te)]
        correct = sum(1 for p, t in zip(preds, y_te) if p == t)
        # Per-class precision/recall/F1 — shows WHICH labels confuse each other,
        # the actionable signal for pushing accuracy up (overall % alone doesn't).
        try:
            from sklearn.metrics import classification_report
            report = classification_report(y_te, preds, zero_division=0)
            LOG.info("Hold-out per-class report:\n%s", report)
        except Exception:
            LOG.debug("classification_report failed", exc_info=True)
        return round(correct / len(y_te), 4) if y_te else None
    except Exception as exc:
        LOG.warning("Hold-out accuracy evaluation failed: %s", exc)
        return None


def _collect_training_data_from_db(
    db: Database,
    min_samples: int = MIN_TRAINING_SAMPLES,
    min_per_class: int = MIN_SAMPLES_PER_CLASS,
) -> Tuple[List[str], List[str], List[FeatureVector]]:
    """Collect labeled training data from the database.

    Uses existing predictions with primary_label as the supervised target.
    Falls back to email labels if no predictions exist.

    Args:
        db: Database instance.
        min_samples: Minimum number of samples required.

    Returns:
        Tuple of (corpus_texts, labels, feature_vectors).

    Raises:
        ValueError: If insufficient labeled data is available.
    """
    corpus: List[str] = []
    labels: List[str] = []
    vectors: List[FeatureVector] = []

    # Strategy 1: Use predictions table as the supervised target, OVERRIDDEN by
    # the most recent user correction when one exists. Closes the learning loop:
    # the human's "actually this is X" beats the model's past guess.
    rows = db.execute_sql(
        """
        SELECT p.email_gmail_id,
               p.primary_label,
               p.llm_label,
               (
                   SELECT uc.corrected_label
                   FROM user_corrections uc
                   WHERE uc.email_gmail_id = p.email_gmail_id
                     AND uc.corrected_label IS NOT NULL
                     AND uc.corrected_label != ''
                   ORDER BY uc.created_at DESC
                   LIMIT 1
               ) AS corrected_label,
               e.subject, e.snippet, e.sender,
               e.body_text, e.date_ts, e.recipients, e.labels, e.user_labels
        FROM predictions p
        JOIN emails e ON e.gmail_id = p.email_gmail_id
        WHERE p.primary_label IS NOT NULL
          AND p.primary_label != ''
        ORDER BY p.created_at DESC
        """
    ).fetchall()

    seen_gmail_ids = set()
    correction_overrides = 0

    for row in rows:
        gmail_id = row["email_gmail_id"]
        if gmail_id in seen_gmail_ids:
            continue
        seen_gmail_ids.add(gmail_id)

        # Priority: explicit correction > your real Gmail label > LLM content
        # label > rules guess. The LLM label sits ABOVE the rules guess so the
        # model learns content-derived labels instead of echoing the rules
        # (which only ever emit NOTIFICATION/NEWSLETTER/MASS_EMAIL/CALENDAR).
        corrected = row["corrected_label"]
        try:
            llm_label = row["llm_label"]
        except (IndexError, KeyError):
            llm_label = None
        user_labels = [x for x in (row["user_labels"] or "").split(",") if x]
        label = (
            corrected
            or (user_labels[0] if user_labels else None)
            or llm_label
            or row["primary_label"]
        )
        if corrected and corrected != row["primary_label"]:
            correction_overrides += 1
        if not label:
            continue
        # NOTE: VALID_LABELS filter removed — taxonomy is now whatever you actually use.

        # Build a text corpus from available fields
        subject = row["subject"] or ""
        snippet = row["snippet"] or ""
        body = row["body_text"] or ""
        sender = row["sender"] or ""
        text = build_model_text(subject, sender, snippet, body)

        if not text:
            continue

        corpus.append(text)
        labels.append(label)

        # Build a FeatureVector for optional use
        # We can't fully construct an Email here, but the text is sufficient
        vectors.append(FeatureVector(
            subject=subject,
            snippet=snippet,
            sender=sender,
            email_gmail_id=gmail_id,
            true_label=label,
        ))

    if len(corpus) >= min_samples:
        # Drop classes with too few examples to be learnable (see MIN_SAMPLES_PER_CLASS).
        from collections import Counter
        label_counts = Counter(labels)
        valid_indices = [i for i, lbl in enumerate(labels)
                         if label_counts[lbl] >= min_per_class]

        original_count = len(corpus)
        if len(valid_indices) < len(labels):
            dropped = len(labels) - len(valid_indices)
            corpus = [corpus[i] for i in valid_indices]
            labels = [labels[i] for i in valid_indices]
            vectors = [vectors[i] for i in valid_indices]
            LOG.info(f"Dropped {dropped} samples with sparse labels (< {min_per_class} examples)")

        LOG.info(
            "Collected %d training samples from predictions table (labels: %s); "
            "%d overridden by user corrections.",
            len(corpus), sorted(set(labels)), correction_overrides,
        )
        return corpus, labels, vectors

    # Strategy 2: Fall back to emails with Gmail labels that map to our categories
    LOG.info(
        f"Insufficient prediction-based samples ({len(corpus)} < {min_samples}). "
        "Attempting email label-based fallback..."
    )

    email_rows = db.execute_sql(
        "SELECT gmail_id, subject, snippet, sender, body_text, date_ts, recipients, labels "
        "FROM emails WHERE parsed = 1 "
        "ORDER BY date_ts DESC LIMIT 500"
    ).fetchall()

    label_map = {
        "INBOX": "NOTIFICATION",
        "UNREAD": "NOTIFICATION",
        "STARRED": "WORK",
        "IMPORTANT": "WORK",
        "CATEGORY_SOCIAL": "PERSONAL",
        "CATEGORY_UPDATES": "NOTIFICATION",
        "CATEGORY_FORUMS": "MASS_EMAIL",
        "CATEGORY_PROMOTIONS": "NEWSLETTER",
        "SPAM": "SPAMCANDIDATE",
        "TRASH": "DEFER",
    }

    seen_ids = set(seen_gmail_ids)
    for row in email_rows:
        gmail_id = row["gmail_id"]
        if gmail_id in seen_ids:
            continue
        seen_ids.add(gmail_id)

        email_labels_str = row["labels"] or ""
        email_label_list = [lbl.strip() for lbl in email_labels_str.split(",") if lbl.strip()]

        # Find first mapped label
        mapped_label = None
        for gmail_label in email_label_list:
            if gmail_label in label_map:
                mapped_label = label_map[gmail_label]
                break

        if not mapped_label:
            continue

        subject = row["subject"] or ""
        snippet = row["snippet"] or ""
        body = row["body_text"] or ""
        sender = row["sender"] or ""
        text = build_model_text(subject, sender, snippet, body)

        if not text:
            continue

        corpus.append(text)
        labels.append(mapped_label)

        vectors.append(FeatureVector(
            subject=subject,
            snippet=snippet,
            sender=sender,
            email_gmail_id=gmail_id,
            true_label=mapped_label,
        ))

    if len(corpus) < min_samples:
        raise ValueError(
            f"Insufficient labeled data for training: {len(corpus)} samples "
            f"(need at least {min_samples}). Run the rules pipeline on more emails first, "
            "or provide labeled data explicitly."
        )

    LOG.info(
        f"Collected {len(corpus)} training samples from email labels "
        f"(labels: {sorted(set(labels))})"
    )
    return corpus, labels, vectors


def train_model_from_db(
    db: Database,
    model_name: str = "pass4_baseline.joblib",
    min_samples: int = MIN_TRAINING_SAMPLES,
    min_accuracy: Optional[float] = None,
) -> Optional[MLClassifier]:
    """Train the ML model using historical data from the database.

    This is the primary training entry point. It:
    1. Collects labeled data from the database
    2. Trains the MLClassifier
    3. Saves the model to disk
    4. Logs training metadata

    Args:
        db: Database instance.
        model_name: Filename for the saved model.
        min_samples: Minimum samples required for training.

    Returns:
        Trained MLClassifier if successful, None if insufficient data.
    """
    try:
        corpus, labels, _vectors = _collect_training_data_from_db(db, min_samples)
    except ValueError as e:
        LOG.warning(f"Cannot train model: {e}")
        return None

    # Count samples per class
    from collections import Counter
    class_counts = Counter(labels)
    LOG.info(f"Training data distribution: {dict(class_counts)}")

    # Filter out classes with too few samples
    valid_indices = [
        i for i, lbl in enumerate(labels)
        if class_counts[lbl] >= MIN_SAMPLES_PER_CLASS
    ]
    if len(valid_indices) < len(labels):
        corpus = [corpus[i] for i in valid_indices]
        labels = [labels[i] for i in valid_indices]

    # Build metadata
    metadata = ModelMetadata(
        version="1.0.0",
        pipeline_used="ml",
        class_names=sorted(set(labels)),
        num_samples=len(corpus),
        features_used=["subject", "snippet", "sender", "body_preview"],
        trained_at=datetime.now(timezone.utc).isoformat(),
    )

    # Explicit sanity checks before fitting to provide clear error messages
    if len(corpus) == 0:
        raise ValueError(
            "Training corpus is empty. Cannot train model without labeled email data. "
            "Run the rules pipeline on more emails first, or provide labeled data explicitly."
        )
    if len(set(labels)) < 2:
        raise ValueError(
            f"Training requires at least two distinct classes, but only {len(set(labels))} found: "
            f"{sorted(set(labels))}. Collect data for at least two different email categories."
        )
    # Check non-empty text examples (simplified)
    if not any(doc.strip() for doc in corpus):
        raise ValueError(
            "Training corpus contains only empty documents. "
            "Ensure emails have non-empty subject, snippet, or body text."
        )

    # Measure hold-out accuracy and gate promotion. An unvalidated or
    # below-floor model is NOT saved, so the previous (live) model stays in
    # place and the inference gate in main._build_classifier_router keeps the
    # ML tier off rather than shipping a regression.
    metadata.accuracy = _evaluate_holdout(corpus, labels)
    LOG.info("Hold-out accuracy: %s", metadata.accuracy)
    if min_accuracy is not None and (
        metadata.accuracy is None or metadata.accuracy < min_accuracy
    ):
        LOG.warning(
            "Retrain NOT promoted: accuracy=%s < floor %.2f — keeping previous model.",
            metadata.accuracy, min_accuracy,
        )
        return None

    # Train the deployed model on the FULL corpus (hold-out was only for scoring).
    classifier = MLClassifier()
    classifier.train(corpus, labels, metadata=metadata)

    # Save
    saved_path = classifier.save(model_name)
    LOG.info(f"Model saved to {saved_path} (accuracy={metadata.accuracy})")

    # Store model metadata in database
    try:
        _save_model_metadata_to_db(db, metadata, model_name)
    except Exception as e:
        LOG.warning(f"Failed to save model metadata to database: {e}")

    return classifier


def train_model_from_data(
    corpus: List[str],
    labels: List[str],
    model_dir: Optional[Path] = None,
    model_name: str = "pass4_baseline.joblib",
) -> MLClassifier:
    """Train the ML model from explicit data (for testing or custom training).

    Args:
        corpus: List of text documents.
        labels: Corresponding labels.
        model_dir: Optional model directory override.
        model_name: Filename for the saved model.

    Returns:
        Trained MLClassifier.
    """
    if not corpus or not labels:
        raise ValueError("Training requires non-empty corpus and labels")
    if len(corpus) != len(labels):
        raise ValueError("Corpus and labels must have the same length")

    metadata = ModelMetadata(
        version="1.0.0",
        pipeline_used="ml",
        class_names=sorted(set(labels)),
        num_samples=len(corpus),
        features_used=["subject", "snippet", "sender", "body_preview"],
        trained_at=datetime.now(timezone.utc).isoformat(),
    )

    classifier = MLClassifier(model_dir=model_dir)
    classifier.train(corpus, labels, metadata=metadata)
    classifier.save(model_name)

    return classifier


def _save_model_metadata_to_db(
    db: Database,
    metadata: ModelMetadata,
    model_name: str,
) -> None:
    """Store model metadata in the system_state table for auditability."""
    key = f"ml_model:{model_name}"
    value = json.dumps(metadata.to_dict())
    db.execute_sql(
        "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, int(datetime.now(timezone.utc).timestamp())),
    )
    LOG.debug(f"Saved ML model metadata to system_state[{key}]")


def get_model_metadata_from_db(db: Database, model_name: str = "pass4_baseline.joblib") -> Optional[dict]:
    """Retrieve model metadata from the database."""
    key = f"ml_model:{model_name}"
    rows = db.execute_sql(
        "SELECT value FROM system_state WHERE key = ?", (key,)
    ).fetchall()
    if rows:
        try:
            return json.loads(rows[0]["value"])
        except (json.JSONDecodeError, KeyError):
            return None
    return None
