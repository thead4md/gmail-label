"""Tests for SafetyPolicy.

These were missing — the audit flagged ZERO test coverage on this file even
though it's the gate between MailMind's automation and real Gmail mutations.
Pinning the safety contract before any automation behavior change in P2A/2B.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from mailmind.actions.safety import SafetyDecision, SafetyPolicy
from mailmind.storage.models import Email


def _email(
    gmail_id: str = "g1",
    sender: str = "alice@example.com",
    labels: Optional[List[str]] = None,
) -> Email:
    return Email(
        gmail_id=gmail_id,
        sender=sender,
        subject="s",
        snippet="x",
        body_text="b",
        recipients=["me@example.com"],
        date_ts=1,
        labels=labels or [],
        parsed=True,
    )


class TestDryRun:
    def test_dry_run_allows_action_but_marks_decision(self):
        policy = SafetyPolicy(dry_run=True)
        decision = policy.evaluate("archive", _email(), confidence=0.9)
        assert decision.allowed is True
        assert "Dry-run" in decision.reason

    def test_dry_run_allows_delete_too(self):
        """In dry-run, even delete is 'allowed' (the executor never actually fires)."""
        policy = SafetyPolicy(dry_run=True)
        decision = policy.evaluate("delete", _email(), confidence=0.99)
        assert decision.allowed is True


class TestNeverAutoDelete:
    def test_live_delete_always_blocked(self):
        policy = SafetyPolicy(dry_run=False)
        decision = policy.evaluate("delete", _email(), confidence=0.99)
        assert decision.allowed is False
        assert "delete" in decision.reason.lower()

    def test_delete_blocked_even_at_max_confidence(self):
        policy = SafetyPolicy(dry_run=False)
        decision = policy.evaluate("delete", _email(), confidence=1.0)
        assert decision.allowed is False


class TestProtectedCategories:
    @pytest.mark.parametrize("category", ["URGENT", "FINANCE", "PERSONAL"])
    def test_cannot_auto_archive_protected_primary_label(self, category):
        policy = SafetyPolicy(dry_run=False)
        decision = policy.evaluate(
            "archive", _email(), confidence=0.99, primary_label=category,
        )
        assert decision.allowed is False
        assert category in decision.reason or "auto-archive" in decision.reason

    @pytest.mark.parametrize("category", ["URGENT", "FINANCE", "PERSONAL"])
    def test_cannot_auto_archive_protected_email_label(self, category):
        """Even if the primary label is OTHER, an email already labelled
        URGENT/FINANCE/PERSONAL in Gmail is protected from auto-archive."""
        policy = SafetyPolicy(dry_run=False)
        email = _email(labels=[category])
        decision = policy.evaluate("archive", email, confidence=0.99,
                                   primary_label="OTHER")
        assert decision.allowed is False

    def test_label_action_on_protected_category_still_allowed(self):
        """Only archive is blocked for protected categories; label is fine."""
        policy = SafetyPolicy(dry_run=False)
        decision = policy.evaluate("label", _email(), confidence=0.99,
                                   primary_label="URGENT")
        assert decision.allowed is True


class TestProtectedSenders:
    def test_protected_sender_blocked_from_archive(self):
        policy = SafetyPolicy(dry_run=False, protected_senders=["boss@x.com"])
        decision = policy.evaluate(
            "archive", _email(sender="boss@x.com"), confidence=0.99,
        )
        assert decision.allowed is False
        assert "protected" in decision.reason.lower()

    def test_protected_sender_case_insensitive(self):
        policy = SafetyPolicy(dry_run=False, protected_senders=["BOSS@X.COM"])
        decision = policy.evaluate(
            "archive", _email(sender="boss@x.com"), confidence=0.99,
        )
        assert decision.allowed is False

    def test_protected_domain_blocked_from_archive(self):
        policy = SafetyPolicy(dry_run=False, protected_domains=["work.com"])
        decision = policy.evaluate(
            "archive", _email(sender="anyone@work.com"), confidence=0.99,
        )
        assert decision.allowed is False

    def test_protected_sender_can_still_be_labelled(self):
        """Protection blocks destructive actions only, not labelling."""
        policy = SafetyPolicy(dry_run=False, protected_senders=["boss@x.com"])
        decision = policy.evaluate(
            "label", _email(sender="boss@x.com"), confidence=0.99,
        )
        assert decision.allowed is True


class TestRateLimiting:
    def test_within_limit_allows(self):
        policy = SafetyPolicy(dry_run=False, max_actions_per_hour=3)
        for _ in range(3):
            assert policy.evaluate("label", _email(), confidence=0.9).allowed

    def test_over_limit_blocks(self):
        policy = SafetyPolicy(dry_run=False, max_actions_per_hour=2)
        policy.evaluate("label", _email(), confidence=0.9)
        policy.evaluate("label", _email(), confidence=0.9)
        decision = policy.evaluate("label", _email(), confidence=0.9)
        assert decision.allowed is False
        assert "rate" in decision.reason.lower()

    def test_old_actions_drop_out_of_window(self):
        policy = SafetyPolicy(dry_run=False, max_actions_per_hour=1)
        # Stuff an old timestamp manually so it gets pruned.
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        policy._action_timestamps.append(old)
        # The old one is pruned by _is_rate_limited, so this new one fits.
        decision = policy.evaluate("label", _email(), confidence=0.9)
        assert decision.allowed is True


class TestAllowedHappyPath:
    def test_label_action_passes_all_gates(self):
        policy = SafetyPolicy(dry_run=False)
        decision = policy.evaluate("label", _email(), confidence=0.9,
                                   primary_label="WORK")
        assert decision.allowed is True
        assert "passed" in decision.reason.lower()
