"""Test that user_labels (truth labels) take priority in training data collection.

This test verifies the label priority: explicit correction > user's Gmail label > rules guess.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction
from mailmind.ml.train import _collect_training_data_from_db


def test_user_labels_override_rules_guess():
    """Seed multiple emails with user_labels='OE/ToDo' and prediction primary_label='NEWSLETTER'.

    Assert the collected training labels are 'OE/ToDo', not 'NEWSLETTER'.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        db = Database(db_path)

        # Insert 5 emails with the same user_labels to meet the 5-sample threshold
        for i in range(5):
            email = Email(
                gmail_id=f"test_gmail_id_{i}",
                subject=f"Test Email {i}",
                snippet="Test snippet",
                sender="test@example.com",
                body_text="Test body",
                labels="INBOX",
            )
            db.insert_email(email)

            # Set the user's truth label
            db.set_email_user_labels(f"test_gmail_id_{i}", "OE/ToDo")

            # Insert a prediction with a different primary_label (the rules guess)
            pred = Prediction(
                email_gmail_id=f"test_gmail_id_{i}",
                model="test",
                labels=["NEWSLETTER"],
                priority_score=50,
                primary_label="NEWSLETTER",
                pipeline_used="rules",
            )
            db.save_prediction(pred)

        # Collect training data
        corpus, labels, vectors = _collect_training_data_from_db(db, min_samples=5)

        # Assert all user truth labels were selected, not the rules guess
        assert len(labels) == 5, f"Expected 5 samples, got {len(labels)}"
        assert all(lbl == "OE/ToDo" for lbl in labels), \
            f"Expected all labels to be 'OE/ToDo', got {set(labels)}"

    finally:
        Path(db_path).unlink(missing_ok=True)


def test_correction_overrides_user_labels():
    """Explicit correction should override even user_labels."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        db = Database(db_path)

        # Insert 5 emails with corrections to meet the 5-sample threshold
        for i in range(5):
            email = Email(
                gmail_id=f"test_gmail_id_2_{i}",
                subject=f"Test Email 2-{i}",
                snippet="Test snippet",
                sender="test@example.com",
                body_text="Test body",
                labels="INBOX",
            )
            db.insert_email(email)
            db.set_email_user_labels(f"test_gmail_id_2_{i}", "OE/ToDo")

            # Insert a prediction
            pred = Prediction(
                email_gmail_id=f"test_gmail_id_2_{i}",
                model="test",
                labels=["NEWSLETTER"],
                priority_score=50,
                primary_label="NEWSLETTER",
                pipeline_used="rules",
            )
            db.save_prediction(pred)

            # Insert a user correction that overrides everything
            db.execute_sql(
                "INSERT INTO user_corrections "
                "(email_gmail_id, original_label, corrected_label, source) "
                "VALUES (?, ?, ?, ?)",
                (f"test_gmail_id_2_{i}", "NEWSLETTER", "WORK", "dashboard"),
            )
            db._conn.commit()

        # Collect training data
        corpus, labels, vectors = _collect_training_data_from_db(db, min_samples=5)

        # Assert the corrections were selected (not user_labels, not primary_label)
        assert len(labels) == 5
        assert all(lbl == "WORK" for lbl in labels), \
            f"Expected all labels to be 'WORK' (correction), got {set(labels)}"

    finally:
        Path(db_path).unlink(missing_ok=True)
