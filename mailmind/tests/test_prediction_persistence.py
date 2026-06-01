"""Real DB persistence test for Prediction with extended schema.

Covers:
- save_prediction() inserts all fields (including Pass 3+ extended columns)
- get_predictions_for_email() retrieves the same fields back
- Rule_matches join/split round-trip via DB
- Scoring_breakdown is valid JSON
- Backward compatibility with schema migrations

This test proves the schema migration (0007) aligns with the code.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pytest

from mailmind.storage.database import Database
from mailmind.storage.models import Prediction


@pytest.fixture
def db():
    """Create a temporary database for persistence tests."""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test_mailmind.db"
    db = Database(str(db_path))
    yield db
    db.close()
    # Cleanup
    if db_path.exists():
        db_path.unlink()
    tmp_dir.rmdir()


class TestPredictionPersistence:
    """Real DB integration tests proving save_prediction() works with migrated schema."""

    def test_save_and_retrieve_all_fields(self, db):
        """Save a Prediction with all extended fields, retrieve, and verify each field."""
        pred = Prediction(
            email_gmail_id="persist_full_001",
            model="hybrid",
            labels=["WORK", "NEWSLETTER"],
            priority_score=65,
            primary_label="WORK",
            confidence=0.78,
            pipeline_used="hybrid",
            action_suggested="star",
            rule_matches=["rule_sender_trusted", "rule_keyword_meeting"],
            scoring_breakdown=json.dumps({"total": 65, "base_score": 50, "ml": {"label": "WORK"}}),
            ml_confidence=0.78,
            llm_confidence=None,
            llm_label="CALENDAR",
            llm_rationale="Meeting invite from a colleague.",
            llm_action_hint="label",
            llm_needs_review=True,
            classifier_source="llm",
            llm_called_at="2026-05-29T12:00:00Z",
            score=65,
        )
        db.save_prediction(pred)

        rows = db.get_predictions_for_email("persist_full_001")
        assert len(rows) == 1
        row = rows[0]

        assert row["email_gmail_id"] == "persist_full_001"
        assert row["model"] == "hybrid"
        assert row["labels"] == "WORK,NEWSLETTER"
        assert row["score"] == 65
        assert row["priority_score"] == 65
        assert row["confidence"] == 0.78
        assert row["primary_label"] == "WORK"
        assert row["pipeline_used"] == "hybrid"
        assert row["action_suggested"] == "star"
        assert row["rule_matches"] == "rule_sender_trusted,rule_keyword_meeting"
        assert row["ml_confidence"] == 0.78
        assert row["llm_confidence"] is None
        assert row["llm_label"] == "CALENDAR"
        assert row["llm_rationale"] == "Meeting invite from a colleague."
        assert row["llm_action_hint"] == "label"
        assert row["llm_needs_review"] == 1
        assert row["classifier_source"] == "llm"
        assert row["llm_called_at"] == "2026-05-29T12:00:00Z"
        assert row["scoring_breakdown"] is not None

        # Verify scoring_breakdown parses as valid JSON
        breakdown = json.loads(row["scoring_breakdown"])
        assert breakdown["total"] == 65
        assert breakdown["ml"]["label"] == "WORK"

        # Verify rule_matches round-trip through split
        reconstructed = row["rule_matches"].split(",") if row["rule_matches"] else []
        assert reconstructed == ["rule_sender_trusted", "rule_keyword_meeting"]

    def test_save_rules_only_prediction(self, db):
        """Save a minimal rules-only prediction (no ML fields)."""
        pred = Prediction(
            email_gmail_id="persist_rules_001",
            model="rules",
            labels=["NOTIFICATION"],
            priority_score=30,
            primary_label="NOTIFICATION",
            confidence=0.85,
            pipeline_used="rules",
            action_suggested=None,
            rule_matches=[],
            scoring_breakdown=json.dumps({"total": 30, "base_score": 30}),
            ml_confidence=None,
            llm_confidence=None,
        )
        db.save_prediction(pred)

        rows = db.get_predictions_for_email("persist_rules_001")
        assert len(rows) == 1
        row = rows[0]

        assert row["primary_label"] == "NOTIFICATION"
        assert row["pipeline_used"] == "rules"
        assert row["rule_matches"] is None  # empty list stored as None
        assert row["ml_confidence"] is None
        assert row["llm_confidence"] is None
        assert row["action_suggested"] is None

    def test_rule_matches_empty_list_stored_as_none(self, db):
        """Empty rule_matches list is stored as None in DB."""
        pred = Prediction(
            email_gmail_id="persist_empty_rules",
            model="rules",
            labels=[],
            priority_score=0,
            primary_label=None,
            confidence=0.0,
            pipeline_used="rules",
            rule_matches=[],
            scoring_breakdown="{}",
        )
        db.save_prediction(pred)

        rows = db.get_predictions_for_email("persist_empty_rules")
        row = rows[0]
        assert row["rule_matches"] is None  # empty list serializes to None

    def test_rule_matches_none_stored_as_none(self, db):
        """None rule_matches is stored as None in DB."""
        pred = Prediction(
            email_gmail_id="persist_null_rules",
            model="rules",
            labels=[],
            priority_score=0,
            primary_label=None,
            confidence=0.0,
            pipeline_used="rules",
            rule_matches=None,
            scoring_breakdown="{}",
        )
        db.save_prediction(pred)

        rows = db.get_predictions_for_email("persist_null_rules")
        row = rows[0]
        assert row["rule_matches"] is None

    def test_rule_matches_roundtrip_via_db(self, db):
        """Rule matches stored and retrieved can be split back to list."""
        original = ["rule_a", "rule_b", "rule_c"]
        pred = Prediction(
            email_gmail_id="persist_roundtrip",
            model="hybrid",
            labels=["WORK"],
            priority_score=50,
            primary_label="WORK",
            confidence=0.85,
            pipeline_used="hybrid",
            rule_matches=original,
            scoring_breakdown="{}",
        )
        db.save_prediction(pred)

        rows = db.get_predictions_for_email("persist_roundtrip")
        row = rows[0]
        stored = row["rule_matches"]
        reconstructed = stored.split(",") if stored else []
        assert reconstructed == original

    def test_resaving_same_email_upserts_one_row(self, db):
        """Re-classifying the same email upserts in place: one row, latest wins.

        Migration 0014 enforces UNIQUE(email_gmail_id); save_prediction uses
        ON CONFLICT DO UPDATE, so the predictions table stays at one row per
        email instead of accumulating a new row on every watch cycle.
        """
        first_id = None
        for i in range(3):
            pred = Prediction(
                email_gmail_id="multi_test_email",
                model="rules",
                labels=["WORK"],
                priority_score=50 + i * 10,
                primary_label="WORK",
                confidence=0.85,
                pipeline_used="rules",
                rule_matches=[],
                scoring_breakdown="{}",
            )
            row_id = db.save_prediction(pred)
            if first_id is None:
                first_id = row_id

        rows = db.get_predictions_for_email("multi_test_email")
        assert len(rows) == 1
        # Latest write wins; the row id is stable across upserts (FK-safe).
        assert rows[0]["priority_score"] == 70
        assert rows[0]["id"] == first_id
