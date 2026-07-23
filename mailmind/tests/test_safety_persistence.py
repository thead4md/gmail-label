"""Tests for SafetyPolicy's db-backed rate-limiter persistence.

This is the correctness net for the scale-to-zero infra change: when a
SafetyPolicy is constructed with a `db`, its action rate limiter must survive
a "process restart" — i.e. a brand new SafetyPolicy instance, backed by the
same db, must still see the actions recorded by a PRIOR instance. Without
this, converting the app to scale-to-zero (a fresh process boots on every
external poll trigger) would silently reset the hourly cap on every restart,
making max_actions_per_hour nearly unenforceable for an app that can
autonomously send emails (Loop Radar) and create calendar events.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mailmind.actions.safety import SafetyPolicy
from mailmind.storage.database import Database
from mailmind.storage.models import Email


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        database = Database(Path(d) / "test.db")
        yield database
        database.close()


def _email(gmail_id: str = "g1", sender: str = "alice@example.com") -> Email:
    return Email(
        gmail_id=gmail_id,
        sender=sender,
        subject="s",
        snippet="x",
        body_text="b",
        recipients=["me@example.com"],
        date_ts=1,
        labels=[],
        parsed=True,
    )


class TestPersistedRateLimitSurvivesRestart:
    def test_cap_survives_fresh_instance_backed_by_same_db(self, db):
        cap = 3
        first = SafetyPolicy(dry_run=False, max_actions_per_hour=cap, db=db)
        for _ in range(cap):
            assert first.evaluate("label", _email(), confidence=0.9).allowed is True

        # Cap reached — the SAME instance is now rate-limited.
        assert first.evaluate("label", _email(), confidence=0.9).allowed is False

        # Simulate a process restart: a brand new SafetyPolicy instance that
        # shares NOTHING in-memory with `first` (no _action_timestamps carried
        # over), backed by the same db.
        second = SafetyPolicy(dry_run=False, max_actions_per_hour=cap, db=db)
        decision = second.evaluate("label", _email(), confidence=0.9)
        assert decision.allowed is False
        assert "rate" in decision.reason.lower()

    def test_without_db_fresh_instance_does_not_inherit_state(self, db):
        """Contrast case: a NON-db-backed policy's limiter is purely
        in-memory (unchanged legacy behavior) — a fresh instance starts
        clean even though the same db exists, because it's never touched."""
        cap = 1
        first = SafetyPolicy(dry_run=False, max_actions_per_hour=cap)
        assert first.evaluate("label", _email(), confidence=0.9).allowed is True
        assert first.evaluate("label", _email(), confidence=0.9).allowed is False

        second = SafetyPolicy(dry_run=False, max_actions_per_hour=cap)
        assert second.evaluate("label", _email(), confidence=0.9).allowed is True

    def test_persisted_timestamps_pruned_after_an_hour(self, db):
        """A persisted timestamp older than an hour is dropped on read, same
        as the in-memory path — the cap is a ROLLING hour, not a permanent
        ceiling a restart could never clear."""
        cap = 1
        policy = SafetyPolicy(dry_run=False, max_actions_per_hour=cap, db=db)
        assert policy.evaluate("label", _email(), confidence=0.9).allowed is True
        assert policy.evaluate("label", _email(), confidence=0.9).allowed is False

        # Manually age the persisted timestamp past the 1-hour window.
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        db.set_state(SafetyPolicy.RATE_LIMIT_STATE_KEY, json.dumps([old.isoformat()]))

        fresh = SafetyPolicy(dry_run=False, max_actions_per_hour=cap, db=db)
        assert fresh.evaluate("label", _email(), confidence=0.9).allowed is True

    def test_corrupt_persisted_state_does_not_crash(self, db):
        """A corrupt/unparsable persisted value must not raise — it's treated
        as empty rather than becoming an outage."""
        db.set_state(SafetyPolicy.RATE_LIMIT_STATE_KEY, "not valid json")
        policy = SafetyPolicy(dry_run=False, max_actions_per_hour=1, db=db)
        assert policy.evaluate("label", _email(), confidence=0.9).allowed is True

    def test_multiple_policies_share_the_cap_via_the_same_db(self, db):
        """Mirrors the real topology: several per-account SafetyPolicy
        instances (e.g. one per mailbox's ActionExecutor) all backed by the
        same db should share ONE effective hourly budget, not one each."""
        cap = 2
        policy_a = SafetyPolicy(dry_run=False, max_actions_per_hour=cap, db=db)
        policy_b = SafetyPolicy(dry_run=False, max_actions_per_hour=cap, db=db)

        assert policy_a.evaluate("label", _email(), confidence=0.9).allowed is True
        assert policy_b.evaluate("label", _email(), confidence=0.9).allowed is True
        # Cap (2) now reached across BOTH instances combined.
        assert policy_a.evaluate("label", _email(), confidence=0.9).allowed is False
        assert policy_b.evaluate("label", _email(), confidence=0.9).allowed is False
