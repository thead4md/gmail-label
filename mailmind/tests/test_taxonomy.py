"""Tests for the canonical email-label taxonomy."""
import pytest
from mailmind.taxonomy import (
    base_score,
    is_known,
    ALL_LABELS,
    BASE_SCORES,
    ML_LABELS,
    DEEPSEEK_LABELS,
    OPENAI_LABELS,
    REVIEW_LABELS,
    DEFAULT_BASE_SCORE,
)


def test_calendar_score():
    """CALENDAR label should score 55 (was implicit 30)."""
    assert base_score("CALENDAR") == 55


def test_mass_email_score():
    """MASS_EMAIL label should score 10 (was implicit 30)."""
    assert base_score("MASS_EMAIL") == 10


def test_base_score_case_insensitive():
    """base_score should handle case-insensitively."""
    assert base_score("calendar") == 55
    assert base_score("CALENDAR") == 55
    assert base_score("Calendar") == 55
    assert base_score("mass_email") == 10
    assert base_score("MASS_EMAIL") == 10


def test_base_score_none():
    """base_score(None) should return DEFAULT_BASE_SCORE."""
    assert base_score(None) == DEFAULT_BASE_SCORE
    assert base_score(None) == 30


def test_base_score_unknown_label():
    """base_score on unknown label should return DEFAULT_BASE_SCORE."""
    assert base_score("NONSENSE") == DEFAULT_BASE_SCORE
    assert base_score("UNKNOWN_LABEL") == DEFAULT_BASE_SCORE
    assert base_score("FAKE") == 30


def test_is_known():
    """is_known should correctly identify known vs unknown labels."""
    assert is_known("CALENDAR") is True
    assert is_known("MASS_EMAIL") is True
    assert is_known("URGENT") is True
    assert is_known("NONSENSE") is False
    assert is_known("UNKNOWN_LABEL") is False
    assert is_known(None) is False


def test_is_known_case_insensitive():
    """is_known should handle case-insensitively."""
    assert is_known("calendar") is True
    assert is_known("Calendar") is True
    assert is_known("mass_email") is True


def test_ml_labels_in_all_labels():
    """All ML_LABELS must be in ALL_LABELS."""
    for label in ML_LABELS:
        assert label in ALL_LABELS, f"ML_LABELS contains {label} not in ALL_LABELS"


def test_deepseek_labels_in_all_labels():
    """All DEEPSEEK_LABELS must be in ALL_LABELS."""
    for label in DEEPSEEK_LABELS:
        assert label in ALL_LABELS, f"DEEPSEEK_LABELS contains {label} not in ALL_LABELS"


def test_openai_labels_in_all_labels():
    """All OPENAI_LABELS must be in ALL_LABELS."""
    for label in OPENAI_LABELS:
        assert label in ALL_LABELS, f"OPENAI_LABELS contains {label} not in ALL_LABELS"


def test_review_labels_in_all_labels():
    """All REVIEW_LABELS must be in ALL_LABELS."""
    for label in REVIEW_LABELS:
        assert label in ALL_LABELS, f"REVIEW_LABELS contains {label} not in ALL_LABELS"


def test_all_labels_have_base_scores():
    """All labels in ALL_LABELS must have entries in BASE_SCORES."""
    for label in ALL_LABELS:
        assert label in BASE_SCORES, f"Label {label} in ALL_LABELS but not in BASE_SCORES"


def test_base_scores_keys_match_all_labels():
    """All keys in BASE_SCORES must be in ALL_LABELS."""
    for label in BASE_SCORES.keys():
        assert label in ALL_LABELS, f"Label {label} in BASE_SCORES but not in ALL_LABELS"


def test_base_scores_range():
    """All base scores should be 0-100."""
    for label, score in BASE_SCORES.items():
        assert 0 <= score <= 100, f"Label {label} has score {score} outside 0-100 range"


def test_urgent_work_finance_personal_scores():
    """High-priority labels should have expected scores."""
    assert base_score("URGENT") == 80
    assert base_score("WORK") == 60
    assert base_score("FINANCE") == 55
    assert base_score("PERSONAL") == 50


def test_low_priority_labels():
    """Low-priority labels should have expected scores."""
    assert base_score("DEFER") == 20
    assert base_score("NEWSLETTER") == 10
    assert base_score("SPAMCANDIDATE") == 5


def test_default_labels():
    """Labels with no explicit mapping should score at DEFAULT_BASE_SCORE."""
    assert base_score("NOTIFICATION") == 30
    assert base_score("ACTION_REQUIRED") == 30
    assert base_score("MEETING") == 30
    assert base_score("RECEIPT") == 30
    assert base_score("SPAM") == 30
    assert base_score("OTHER") == 30
