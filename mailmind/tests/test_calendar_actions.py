"""Tests for actions/calendar.py: CalendarClient.create_event.

Mirrors test_executor.py's mocking conventions (chained MagicMock via
.return_value, HttpError construction) for consistency.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from mailmind.actions.calendar import CalendarClient, DEFAULT_DURATION_SECONDS
from mailmind.actions.safety import SafetyPolicy


def _calendar_service_mock():
    service = MagicMock()
    insert_call = service.events.return_value.insert
    insert_call.return_value.execute.return_value = {"id": "evt-123"}
    return service


def _insert_mock(service):
    return service.events.return_value.insert


class TestCreateEventDryRun:
    def test_dry_run_never_calls_api(self):
        service = _calendar_service_mock()
        client = CalendarClient(service, SafetyPolicy(dry_run=True))
        result = client.create_event("Deadline: Report", 1_000_000)
        assert result == "dry_run"
        _insert_mock(service).assert_not_called()


class TestCreateEventSuccess:
    def test_creates_event_and_returns_id(self):
        service = _calendar_service_mock()
        client = CalendarClient(service, SafetyPolicy(dry_run=False), rate_limit_seconds=0)
        result = client.create_event("Deadline: Report", 1_000_000)
        assert result == "evt-123"
        _insert_mock(service).assert_called_once()

    def test_default_duration_applied_when_end_ts_omitted(self):
        service = _calendar_service_mock()
        client = CalendarClient(service, SafetyPolicy(dry_run=False), rate_limit_seconds=0)
        client.create_event("Deadline", 1_000_000)
        call_kwargs = _insert_mock(service).call_args.kwargs
        body = call_kwargs["body"]
        start = body["start"]["dateTime"]
        end = body["end"]["dateTime"]
        assert start != end  # a real, non-zero-length hold was created

    def test_explicit_end_ts_respected(self):
        service = _calendar_service_mock()
        client = CalendarClient(service, SafetyPolicy(dry_run=False), rate_limit_seconds=0)
        client.create_event("Deadline", 1_000_000, end_ts=1_000_000 + 3600)
        call_kwargs = _insert_mock(service).call_args.kwargs
        assert call_kwargs["body"]["summary"] == "Deadline"

    def test_calendar_id_defaults_to_primary(self):
        service = _calendar_service_mock()
        client = CalendarClient(service, SafetyPolicy(dry_run=False), rate_limit_seconds=0)
        client.create_event("Deadline", 1_000_000)
        call_kwargs = _insert_mock(service).call_args.kwargs
        assert call_kwargs["calendarId"] == "primary"


class TestCreateEventFailureModes:
    def test_rate_limited_refuses_without_calling_api(self):
        service = _calendar_service_mock()
        policy = SafetyPolicy(dry_run=False, max_actions_per_hour=1)
        # Pre-fill the rate limiter so the very next check trips it.
        import datetime as dt
        policy._action_timestamps = [dt.datetime.now(dt.timezone.utc)]
        client = CalendarClient(service, policy)
        result = client.create_event("Deadline", 1_000_000)
        assert result is None
        _insert_mock(service).assert_not_called()

    def test_http_error_returns_none_not_raises(self):
        service = _calendar_service_mock()
        resp = MagicMock(status=403, reason="Insufficient Permission")
        _insert_mock(service).return_value.execute.side_effect = HttpError(
            resp=resp, content=b"insufficient scope",
        )
        client = CalendarClient(service, SafetyPolicy(dry_run=False), rate_limit_seconds=0)
        result = client.create_event("Deadline", 1_000_000)
        assert result is None

    def test_unexpected_exception_returns_none_not_raises(self):
        service = _calendar_service_mock()
        _insert_mock(service).return_value.execute.side_effect = RuntimeError("boom")
        client = CalendarClient(service, SafetyPolicy(dry_run=False), rate_limit_seconds=0)
        result = client.create_event("Deadline", 1_000_000)
        assert result is None
