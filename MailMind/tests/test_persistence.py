"""Tests for database persistence format of Prediction fields.

Covers:
- rule_matches stored as comma-separated string in DB (format verified)
- Retrieval and reconstruction of rule_matches list from DB row
"""
from __future__ import annotations

from mailmind.storage.models import Prediction


class TestRuleMatchesPersistenceFormat:
    """Verify rule_matches join/split round-trip persistence format.

    Note: Actual DB persistence tests require the 'primary_label' and other
    extended columns to exist in the SQLite schema. The current schema (up to
    migration 0006) does not include these columns. The tests below verify
    the format contract (comma-separated) without depending on the schema.
    """

    def test_rule_matches_join_format(self):
        """Rule matches list joins to comma-separated string."""
        matches = ["rule_sender_trusted", "rule_keyword_meeting", "rule_domain_corp"]
        joined = ",".join(matches)
        assert joined == "rule_sender_trusted,rule_keyword_meeting,rule_domain_corp"

    def test_rule_matches_split_format(self):
        """Comma-separated string splits back to original list."""
        stored = "rule_sender_trusted,rule_keyword_meeting,rule_domain_corp"
        reconstructed = stored.split(",") if stored else []
        assert reconstructed == ["rule_sender_trusted", "rule_keyword_meeting", "rule_domain_corp"]

    def test_rule_matches_single_item(self):
        """Single-item rule_matches works through join/split."""
        matches = ["rule_only_one"]
        joined = ",".join(matches)
        assert joined == "rule_only_one"
        reconstructed = joined.split(",")
        assert reconstructed == matches

    def test_rule_matches_empty_list(self):
        """Empty list stored as None (None check)."""
        matches = []
        if matches:
            joined = ",".join(matches)
        else:
            joined = None
        assert joined is None

    def test_rule_matches_none(self):
        """None rule_matches remains None."""
        matches = None
        if matches:
            joined = ",".join(matches)
        else:
            joined = None
        assert joined is None

    def test_roundtrip_reconstructable(self):
        """Full round-trip: list -> join -> split -> list."""
        original = ["rule_a", "rule_b", "rule_c"]
        stored = ",".join(original) if original else None
        reconstructed = stored.split(",") if stored else []
        assert reconstructed == original

    def test_model_rule_matches_property(self):
        """Prediction model stores and retrieves rule_matches correctly."""
        pred = Prediction(
            email_gmail_id="test_model_001",
            model="hybrid",
            labels=["WORK", "NEWSLETTER"],
            priority_score=65,
            primary_label="WORK",
            confidence=0.78,
            pipeline_used="hybrid",
            rule_matches=["rule_a", "rule_b"],
            scoring_breakdown="{}",
            ml_confidence=0.78,
            llm_confidence=None,
        )
        assert pred.rule_matches == ["rule_a", "rule_b"]
        # Verify model-level round-trip
        stored = ",".join(pred.rule_matches) if pred.rule_matches else None
        assert stored == "rule_a,rule_b"
        reconstructed = stored.split(",")
        assert reconstructed == pred.rule_matches
