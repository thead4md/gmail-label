"""Tests for label priority weighting in the scorer."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from mailmind.processing.scorer import PriorityScorer
from mailmind.storage.database import Database
from mailmind.storage.models import Email, SenderReputation


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        database = Database(Path(d) / "t.db")
        yield database
        database.close()


def _old_timestamp():
    """Return a timestamp from 30 days ago (no recency bonus)."""
    return int(time.time()) - 30 * 86400


def test_set_and_get_label_priority(db):
    """Test round-trip: set and retrieve label priority."""
    db.set_label_priority("WORK", 10)
    db.set_label_priority("NEWSLETTER", -15)

    priorities = db.get_label_priorities()
    assert priorities["WORK"] == 10
    assert priorities["NEWSLETTER"] == -15


def test_label_priority_clamped_to_range(db):
    """Test that weights are clamped to -20..+30."""
    db.set_label_priority("URGENT", 50)  # Clamp to +30
    db.set_label_priority("DEFER", -50)  # Clamp to -20

    priorities = db.get_label_priorities()
    assert priorities["URGENT"] == 30
    assert priorities["DEFER"] == -20


def test_label_priority_update(db):
    """Test updating an existing label priority."""
    db.set_label_priority("WORK", 5)
    db.set_label_priority("WORK", 15)

    priorities = db.get_label_priorities()
    assert priorities["WORK"] == 15


def test_scorer_adds_label_priority_weight(db):
    """Test that compute_score includes label priority weight."""
    db.set_label_priority("URGENT", 10)

    scorer = PriorityScorer()

    # Old email so no recency bonus, no recipients for direct mention bonus
    email = Email(
        gmail_id="msg1",
        thread_id="t1",
        sender="alice@example.com",
        recipients=[],
        subject="Test",
        date_ts=_old_timestamp(),
        labels=["URGENT"],
    )

    result = scorer.compute_score(email, [], db=db)

    # Base score for URGENT is 80, + label priority weight of 10 = 90
    assert result.base_score == 80
    assert result.label_priority_weight == 10
    assert result.total_score == 90


def test_scorer_high_weight_raises_score(db):
    """Test that a high-weight label raises total_score compared to zero weight."""
    scorer = PriorityScorer()

    email = Email(
        gmail_id="msg2a",
        thread_id="t2",
        sender="news@example.com",
        recipients=[],
        subject="Newsletter",
        date_ts=_old_timestamp(),
        labels=["NEWSLETTER"],
    )

    # Score without weight
    result_no_weight = scorer.compute_score(email, [], db=db)

    # Set high weight and score again
    db.set_label_priority("NEWSLETTER", 20)
    result_with_weight = scorer.compute_score(email, [], db=db)

    # With weight should be higher (newsletter has base 10, then gets -20 penalty, but weight adds back)
    assert result_with_weight.label_priority_weight == 20
    assert result_with_weight.total_score > result_no_weight.total_score


def test_scorer_zero_config_unchanged_scores(db):
    """Test that scores are unchanged when no priorities are set."""
    scorer = PriorityScorer()

    email = Email(
        gmail_id="msg3",
        thread_id="t3",
        sender="bob@example.com",
        recipients=[],
        subject="Test",
        date_ts=_old_timestamp(),
        labels=["WORK"],
    )

    result = scorer.compute_score(email, [], db=db)

    # Base score for WORK is 60, no weight
    assert result.base_score == 60
    assert result.label_priority_weight == 0
    assert result.total_score == 60


def test_scorer_negative_weight_lowers_score(db):
    """Test that a negative weight lowers total_score compared to zero weight."""
    db.set_label_priority("NOTIFICATION", -10)

    scorer = PriorityScorer()

    email = Email(
        gmail_id="msg4",
        thread_id="t4",
        sender="notify@example.com",
        recipients=[],
        subject="Notification",
        date_ts=_old_timestamp(),
        labels=["NOTIFICATION"],
    )

    result = scorer.compute_score(email, [], db=db)

    # Base score for NOTIFICATION is 30, - weight 10 = 20
    assert result.base_score == 30
    assert result.label_priority_weight == -10
    assert result.total_score == 20


def test_scorer_weight_clamped_in_final_score(db):
    """Test that final score is clamped 0-100."""
    db.set_label_priority("URGENT", 50)  # Weight will be clamped to 30 in DB

    scorer = PriorityScorer()

    email = Email(
        gmail_id="msg5",
        thread_id="t5",
        sender="urgent@example.com",
        recipients=[],
        subject="Urgent",
        date_ts=_old_timestamp(),
        labels=["URGENT"],
    )

    result = scorer.compute_score(email, [], db=db)

    # Base score 80 + weight 30 = 110, clamped to 100
    assert result.total_score == 100


def test_scorer_breakdown_includes_label_priority(db):
    """Test that scoring breakdown includes label_priority_weight."""
    db.set_label_priority("WORK", 5)

    scorer = PriorityScorer()

    email = Email(
        gmail_id="msg6",
        thread_id="t6",
        sender="work@example.com",
        recipients=[],
        subject="Work Email",
        date_ts=_old_timestamp(),
        labels=["WORK"],
    )

    result = scorer.compute_score(email, [], db=db)

    # Breakdown should mention the label priority weight
    assert "Label priority weight: 5" in result.breakdown_text


def test_get_label_priorities_empty_when_none_set(db):
    """Test that get_label_priorities returns empty dict when none are set."""
    priorities = db.get_label_priorities()
    assert priorities == {}
