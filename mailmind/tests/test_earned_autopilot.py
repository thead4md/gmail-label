"""Tests for P2B: earned autopilot (per-sender auto-execute opt-in).

Before: any prediction with confidence >= 0.90 auto-executed against
Gmail regardless of who sent the email.

After: auto-execute requires BOTH the 0.90 confidence floor AND
sender_profiles.auto_action_eligible = 1 for that specific sender.
Everyone else queues for review. The 0.90 floor itself is unchanged —
this only narrows when it fires. See CONTEXT.md Decisions Log.
"""
from __future__ import annotations

import pytest

from mailmind.processing.queue_manager import QueueManager
from mailmind.processing.scorer import ScoreResult
from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction
from mailmind.storage.queries import (
    is_sender_auto_action_eligible,
    toggle_sender_auto_action,
)


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def _seed_email_and_prediction(db: Database, sender: str = "alice@example.com"):
    db.insert_email(Email(
        gmail_id="g1",
        sender=sender,
        subject="s",
        snippet="x",
        body_text="b",
        recipients=["me@example.com"],
        date_ts=1,
        labels=[],
        parsed=True,
    ))
    pred = Prediction(
        email_gmail_id="g1",
        model="rules",
        labels=["INBOX"],
        priority_score=95,
        action_suggested="label",
        primary_label="WORK",
        confidence=0.95,
        pipeline_used="rules",
        rule_matches=[],
        scoring_breakdown="{}",
    )
    pred.id = db.save_prediction(pred)
    return pred


def _seed_sender_profile(db: Database, sender: str = "alice@example.com",
                         eligible: bool = False):
    db.execute_sql(
        "INSERT OR REPLACE INTO sender_profiles "
        "(sender_email, total_seen, total_approved, total_rejected, "
        " trust_tier, auto_action_eligible) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sender, 1, 0, 0, "neutral", int(eligible)),
    )
    db._conn.commit()


def _score(total: int = 95) -> ScoreResult:
    return ScoreResult(
        total_score=total,
        base_score=total,
        rule_contribution=0,
        direct_mention_bonus=0,
        recency_bonus=0,
        sender_trust=0,
        primary_label="WORK",
    )


class TestSenderEligibilityHelper:
    def test_no_profile_means_not_eligible(self, db: Database):
        assert is_sender_auto_action_eligible(db, "stranger@x.com") is False

    def test_profile_without_flag_means_not_eligible(self, db: Database):
        _seed_sender_profile(db, "alice@example.com", eligible=False)
        assert is_sender_auto_action_eligible(db, "alice@example.com") is False

    def test_profile_with_flag_means_eligible(self, db: Database):
        _seed_sender_profile(db, "alice@example.com", eligible=True)
        assert is_sender_auto_action_eligible(db, "alice@example.com") is True

    def test_none_sender_returns_false(self, db: Database):
        assert is_sender_auto_action_eligible(db, None) is False

    def test_toggle_helper_round_trips(self, db: Database):
        _seed_sender_profile(db, "alice@example.com", eligible=False)
        toggle_sender_auto_action(db, "alice@example.com", True)
        assert is_sender_auto_action_eligible(db, "alice@example.com") is True
        toggle_sender_auto_action(db, "alice@example.com", False)
        assert is_sender_auto_action_eligible(db, "alice@example.com") is False


class TestEarnedAutopilotGate:
    def test_unauthorised_sender_queues_instead_of_executing(self, db: Database, mocker=None):
        """High confidence + sender NOT eligible -> queue, no Gmail call."""
        from unittest.mock import MagicMock
        executor = MagicMock()
        qm = QueueManager(executor=executor)
        pred = _seed_email_and_prediction(db)
        _seed_sender_profile(db, "alice@example.com", eligible=False)

        email = Email(gmail_id="g1", sender="alice@example.com")
        status = qm.enqueue_from_prediction(db, email, _score(95), pred)

        assert status == "queued"  # NOT 'executed'
        executor.execute_action.assert_not_called()

    def test_unknown_sender_queues_instead_of_executing(self, db: Database):
        """High confidence + sender has no profile at all -> queue, no Gmail call."""
        from unittest.mock import MagicMock
        executor = MagicMock()
        qm = QueueManager(executor=executor)
        pred = _seed_email_and_prediction(db, sender="stranger@x.com")
        # No sender_profiles row at all.

        email = Email(gmail_id="g1", sender="stranger@x.com")
        status = qm.enqueue_from_prediction(db, email, _score(95), pred)

        assert status == "queued"
        executor.execute_action.assert_not_called()

    def test_authorised_sender_auto_executes(self, db: Database):
        """High confidence + sender eligible -> Gmail action fires."""
        from unittest.mock import MagicMock
        executor = MagicMock()
        qm = QueueManager(executor=executor)
        pred = _seed_email_and_prediction(db)
        _seed_sender_profile(db, "alice@example.com", eligible=True)

        email = Email(gmail_id="g1", sender="alice@example.com")
        status = qm.enqueue_from_prediction(db, email, _score(95), pred)

        assert status == "executed"
        executor.execute_action.assert_called_once()

    def test_authorised_sender_but_low_confidence_still_queues(self, db: Database):
        """Eligibility alone is not enough — 0.90 confidence floor still applies.

        The gate uses prediction.confidence (classification certainty), not the
        priority score. Set confidence=0.80 to stay below the 0.90 threshold.
        """
        from unittest.mock import MagicMock
        executor = MagicMock()
        qm = QueueManager(executor=executor)
        pred = _seed_email_and_prediction(db)
        pred.confidence = 0.80  # below AUTO_EXECUTE_THRESHOLD (0.90)
        _seed_sender_profile(db, "alice@example.com", eligible=True)

        email = Email(gmail_id="g1", sender="alice@example.com")
        status = qm.enqueue_from_prediction(db, email, _score(95), pred)

        assert status == "queued"
        executor.execute_action.assert_not_called()
