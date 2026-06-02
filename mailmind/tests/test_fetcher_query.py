"""Tests for GmailFetcher.list_message_ids query support (backfill date range)."""
from __future__ import annotations

from unittest.mock import MagicMock

from mailmind.ingestion.fetcher import GmailFetcher


def _service_returning(ids):
    """Build a mock Gmail service whose messages().list().execute() returns ids."""
    service = MagicMock()
    execute = service.users.return_value.messages.return_value.list.return_value.execute
    execute.return_value = {"messages": [{"id": i} for i in ids]}
    return service


def test_query_is_passed_as_q_param():
    service = _service_returning(["a", "b"])
    f = GmailFetcher(service, rate_limit_seconds=0)
    ids = f.list_message_ids(label_ids=["INBOX"], max_results=10, query="newer_than:3m")

    assert ids == ["a", "b"]
    # Inspect the kwargs the API .list() was called with
    _, kwargs = service.users.return_value.messages.return_value.list.call_args
    assert kwargs["q"] == "newer_than:3m"
    assert kwargs["labelIds"] == ["INBOX"]


def test_query_defaults_to_none():
    service = _service_returning(["x"])
    f = GmailFetcher(service, rate_limit_seconds=0)
    f.list_message_ids(label_ids=["INBOX", "UNREAD"], max_results=5)

    _, kwargs = service.users.return_value.messages.return_value.list.call_args
    assert kwargs["q"] is None


def test_max_results_caps_returned_ids():
    service = _service_returning([str(i) for i in range(20)])
    f = GmailFetcher(service, rate_limit_seconds=0)
    ids = f.list_message_ids(label_ids=["INBOX"], max_results=5, query="newer_than:3m")
    assert len(ids) == 5
