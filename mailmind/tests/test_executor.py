"""Tests for ActionExecutor.

This is the file that actually mutates Gmail — and the audit found ZERO
test coverage. Pinning the executor contract before any automation behavior
change in P2A/2B.
"""
from __future__ import annotations

from typing import List, Optional
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from mailmind.actions.executor import CONFIDENCE_THRESHOLDS, ActionExecutor
from mailmind.actions.safety import SafetyPolicy
from mailmind.processing.scorer import ScoreResult
from mailmind.storage.database import Database
from mailmind.storage.models import Email


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


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


def _score(total: int = 90, primary: str = "WORK") -> ScoreResult:
    return ScoreResult(
        total_score=total,
        base_score=total,
        rule_contribution=0,
        direct_mention_bonus=0,
        recency_bonus=0,
        sender_trust=0,
        primary_label=primary,
    )


def _gmail_service_mock():
    """Gmail service mock.

    The chained-mock trick: every `.method()` call would return a new
    auto-mock, so we wire each chain to a fixed return value. To avoid the
    setup itself counting as a call to ``modify``, we use return_value on
    the unbound method (no parens) so reads of `.modify` don't fire it.
    """
    service = MagicMock()
    # labels().list() -> .execute() returns no existing labels (forces create path)
    list_call = service.users.return_value.labels.return_value.list
    list_call.return_value.execute.return_value = {"labels": []}
    # labels().create() -> .execute() returns a fake label id
    create_call = service.users.return_value.labels.return_value.create
    create_call.return_value.execute.return_value = {"id": "Label_42"}
    # messages().modify() -> .execute() returns empty body
    modify_call = service.users.return_value.messages.return_value.modify
    modify_call.return_value.execute.return_value = {}
    return service


def _modify_mock(service):
    """The single 'modify' MagicMock that records every call from the executor."""
    return service.users.return_value.messages.return_value.modify


def _make_executor(db: Database, *, dry_run: bool, service=None,
                   protected_senders=None, protected_domains=None,
                   max_actions_per_hour=50):
    service = service or _gmail_service_mock()
    policy = SafetyPolicy(
        dry_run=dry_run,
        protected_senders=protected_senders,
        protected_domains=protected_domains,
        max_actions_per_hour=max_actions_per_hour,
    )
    return ActionExecutor(service=service, db=db, safety_policy=policy,
                          rate_limit_seconds=0), service


class TestSuggestAction:
    def test_high_score_suggests_star(self, db: Database):
        executor, _ = _make_executor(db, dry_run=True)
        assert executor.suggest_action(_email(), _score(total=85)) == "star"

    def test_mid_score_suggests_mark_important(self, db: Database):
        executor, _ = _make_executor(db, dry_run=True)
        assert executor.suggest_action(_email(), _score(total=75)) == "mark_important"

    def test_newsletter_high_confidence_suggests_archive(self, db: Database):
        executor, _ = _make_executor(db, dry_run=True)
        # NEWSLETTER + score >= 60 first hits mark_important. To exercise the
        # archive path we need score <60 (so mark_important is skipped) but
        # confidence (=score/100) still >= 0.85, which is impossible. So in
        # practice archive is unreachable via suggest_action — pin that.
        assert executor.suggest_action(
            _email(), _score(total=50, primary="NEWSLETTER")
        ) == "label"

    def test_low_score_returns_none(self, db: Database):
        executor, _ = _make_executor(db, dry_run=True)
        assert executor.suggest_action(_email(), _score(total=40)) is None


class TestExecuteActionDryRun:
    def test_dry_run_never_calls_gmail(self, db: Database):
        executor, service = _make_executor(db, dry_run=True)
        ok = executor.execute_action(_email(), "label", _score(total=90))
        assert ok is True
        # No Gmail mutations at all in dry-run.
        _modify_mock(service).assert_not_called()
        service.users.return_value.labels.return_value.create.assert_not_called()


class TestExecuteActionBlocked:
    def test_delete_blocked_in_live_mode(self, db: Database):
        executor, service = _make_executor(db, dry_run=False)
        ok = executor.execute_action(_email(), "delete", _score(total=100))
        assert ok is False
        _modify_mock(service).assert_not_called()

    def test_low_confidence_defers(self, db: Database):
        executor, service = _make_executor(db, dry_run=False)
        # score 50 -> confidence 0.50 < label threshold 0.65 -> deferred.
        ok = executor.execute_action(_email(), "label", _score(total=50))
        assert ok is False
        _modify_mock(service).assert_not_called()

    def test_protected_sender_blocks_archive(self, db: Database):
        executor, service = _make_executor(
            db, dry_run=False, protected_senders=["boss@x.com"],
        )
        ok = executor.execute_action(
            _email(sender="boss@x.com"), "archive", _score(total=90, primary="NEWSLETTER"),
        )
        assert ok is False
        _modify_mock(service).assert_not_called()

    def test_urgent_label_blocks_archive(self, db: Database):
        executor, service = _make_executor(db, dry_run=False)
        ok = executor.execute_action(
            _email(), "archive", _score(total=90, primary="URGENT"),
        )
        assert ok is False
        _modify_mock(service).assert_not_called()


class TestExecuteActionLive:
    def test_label_calls_gmail_modify(self, db: Database):
        executor, service = _make_executor(db, dry_run=False)
        ok = executor.execute_action(_email(), "label", _score(total=90))
        assert ok is True
        # modify() was actually called for the addLabelIds path.
        modify = _modify_mock(service)
        assert modify.call_count >= 1
        # Last call's body adds a label id.
        kwargs = modify.call_args.kwargs
        assert "addLabelIds" in kwargs["body"]
        assert kwargs["body"]["addLabelIds"] == ["Label_42"]

    def test_star_adds_starred_label(self, db: Database):
        executor, service = _make_executor(db, dry_run=False)
        ok = executor.execute_action(_email(), "star", _score(total=90))
        assert ok is True
        # The STARRED add should appear in the modify body.
        bodies = [c.kwargs.get("body", {}) for c in
                  _modify_mock(service).call_args_list]
        assert any(b.get("addLabelIds") == ["STARRED"] for b in bodies)

    def test_archive_removes_inbox_label(self, db: Database):
        executor, service = _make_executor(db, dry_run=False)
        ok = executor.execute_action(
            _email(), "archive", _score(total=90, primary="NEWSLETTER"),
        )
        assert ok is True
        bodies = [c.kwargs.get("body", {}) for c in
                  _modify_mock(service).call_args_list]
        assert any(b.get("removeLabelIds") == ["INBOX"] for b in bodies)

    def test_mark_important_adds_important_label(self, db: Database):
        executor, service = _make_executor(db, dry_run=False)
        ok = executor.execute_action(_email(), "mark_important", _score(total=80))
        assert ok is True
        bodies = [c.kwargs.get("body", {}) for c in
                  _modify_mock(service).call_args_list]
        assert any(b.get("addLabelIds") == ["IMPORTANT"] for b in bodies)


class TestExecuteActionUnknown:
    def test_unknown_action_returns_false(self, db: Database):
        executor, service = _make_executor(db, dry_run=False)
        ok = executor.execute_action(_email(), "burn_it_all", _score(total=99))
        assert ok is False
        _modify_mock(service).assert_not_called()


class TestGmailErrorHandling:
    def test_http_error_is_caught_and_logged(self, db: Database):
        service = _gmail_service_mock()
        # Make the modify call raise an HttpError.
        resp = MagicMock(status=429, reason="Rate Limit Exceeded")
        _modify_mock(service).return_value.execute.side_effect = HttpError(
            resp=resp, content=b"rate limited",
        )
        executor, _ = _make_executor(db, dry_run=False, service=service)

        # Must not raise — error is caught and returned as False.
        ok = executor.execute_action(_email(), "star", _score(total=90))
        assert ok is False


class TestConfidenceThresholdsContract:
    def test_delete_threshold_is_unreachable(self):
        """delete=1.00 is a sentinel: real confidences are < 1.0."""
        assert CONFIDENCE_THRESHOLDS["delete"] >= 1.0

    def test_archive_is_strictest_executable_threshold(self):
        executable = {k: v for k, v in CONFIDENCE_THRESHOLDS.items() if k != "delete"}
        assert executable["archive"] == max(executable.values())
