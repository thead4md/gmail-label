"""Tests for P1B: training overrides predictions.primary_label with the most
recent user_corrections.corrected_label, closing the learning loop.
"""
from __future__ import annotations

import pytest

from mailmind.ml.train import _collect_training_data_from_db
from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def _email(gmail_id: str, subject: str = "subj") -> Email:
    return Email(
        gmail_id=gmail_id,
        sender="alice@example.com",
        subject=subject,
        snippet="s",
        body_text="this is body text with enough content to train on",
        recipients=["me@example.com"],
        date_ts=1,
        labels=[],
        parsed=True,
    )


def _pred(gmail_id: str, label: str) -> Prediction:
    return Prediction(
        email_gmail_id=gmail_id,
        model="rules",
        labels=[label],
        priority_score=50,
        primary_label=label,
        confidence=0.9,
        pipeline_used="rules",
        rule_matches=[],
        scoring_breakdown="{}",
    )


def _log_correction(db: Database, gmail_id: str, corrected: str, ts: int):
    db.execute_sql(
        "INSERT INTO user_corrections "
        "(email_gmail_id, original_label, corrected_label, source, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (gmail_id, "WORK", corrected, "dashboard", ts),
    )
    db._conn.commit()


def _seed_min_samples(db: Database, n: int = 10):
    """Seed n distinct emails+predictions so min_samples=10 is satisfied."""
    for i in range(n):
        gid = f"filler_{i}"
        db.insert_email(_email(gid, subject=f"sub {i}"))
        # Alternate labels so we have >=2 classes for trainability checks elsewhere.
        db.save_prediction(_pred(gid, "WORK" if i % 2 == 0 else "NEWSLETTER"))


class TestCorrectionOverridesPrimaryLabel:
    def test_correction_overrides_primary(self, db: Database):
        # 10 filler rows (labelled WORK / NEWSLETTER) + 1 target row.
        _seed_min_samples(db, n=10)
        db.insert_email(_email("target"))
        db.save_prediction(_pred("target", "NEWSLETTER"))
        _log_correction(db, "target", "WORK", ts=1000)

        corpus, labels, vectors = _collect_training_data_from_db(db, min_samples=10, min_per_class=1)

        # Find the row our target email contributed.
        idx = next(i for i, v in enumerate(vectors) if v.email_gmail_id == "target")
        # Correction wins: label should be WORK, not the machine's NEWSLETTER guess.
        assert labels[idx] == "WORK"
        assert vectors[idx].true_label == "WORK"

    def test_most_recent_correction_wins(self, db: Database):
        _seed_min_samples(db, n=10)
        db.insert_email(_email("target"))
        db.save_prediction(_pred("target", "NEWSLETTER"))
        # An earlier correction to MASS_EMAIL, then a later one to WORK.
        _log_correction(db, "target", "MASS_EMAIL", ts=1000)
        _log_correction(db, "target", "WORK", ts=2000)

        _corpus, labels, vectors = _collect_training_data_from_db(db, min_samples=10, min_per_class=1)
        idx = next(i for i, v in enumerate(vectors) if v.email_gmail_id == "target")
        assert labels[idx] == "WORK"

    def test_no_correction_uses_primary_label(self, db: Database):
        _seed_min_samples(db, n=10)
        db.insert_email(_email("untouched"))
        db.save_prediction(_pred("untouched", "NEWSLETTER"))
        # No user_corrections row for this email.

        _corpus, labels, vectors = _collect_training_data_from_db(db, min_samples=10, min_per_class=1)
        idx = next(i for i, v in enumerate(vectors) if v.email_gmail_id == "untouched")
        assert labels[idx] == "NEWSLETTER"
