"""Tests for List-Unsubscribe header parsing in parser.py."""
from __future__ import annotations

import pytest

from mailmind.ingestion.parser import _extract_unsubscribe_url, parse_message


def test_extract_unsubscribe_url_https_only():
    """Test extraction of HTTPS URL from List-Unsubscribe header."""
    header = "<https://example.com/unsubscribe>"
    result = _extract_unsubscribe_url(header)
    assert result == "https://example.com/unsubscribe"


def test_extract_unsubscribe_url_https_preferred():
    """Test that HTTPS URL is preferred over mailto."""
    header = "<https://example.com/unsub>, <mailto:unsub@example.com>"
    result = _extract_unsubscribe_url(header)
    assert result == "https://example.com/unsub"


def test_extract_unsubscribe_url_mailto_fallback():
    """Test fallback to mailto when no HTTPS URL."""
    header = "<mailto:unsub@example.com>"
    result = _extract_unsubscribe_url(header)
    assert result == "mailto:unsub@example.com"


def test_extract_unsubscribe_url_multiple_https():
    """Test extraction of first HTTPS URL when multiple present."""
    header = "<https://example.com/unsub1>, <https://example.com/unsub2>"
    result = _extract_unsubscribe_url(header)
    assert result == "https://example.com/unsub1"


def test_extract_unsubscribe_url_no_urls():
    """Test None when no valid URLs in header."""
    header = "some text without urls"
    result = _extract_unsubscribe_url(header)
    assert result is None


def test_extract_unsubscribe_url_empty_string():
    """Test None for empty header."""
    result = _extract_unsubscribe_url("")
    assert result is None


def test_extract_unsubscribe_url_none():
    """Test None for None input."""
    result = _extract_unsubscribe_url(None)
    assert result is None


def test_extract_unsubscribe_url_case_insensitive():
    """Test case-insensitive URL scheme matching."""
    header = "<HTTPS://example.com/unsub>"
    result = _extract_unsubscribe_url(header)
    assert result == "HTTPS://example.com/unsub"


def test_parse_message_with_list_unsubscribe():
    """Test that parse_message extracts unsubscribe_url."""
    resource = {
        "id": "test_123",
        "threadId": "thread_456",
        "snippet": "Test email",
        "labelIds": [],
        "payload": {
            "headers": [
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Test"},
                {"name": "Date", "value": "Mon, 01 Jan 2024 12:00:00 +0000"},
                {"name": "List-Unsubscribe", "value": "<https://example.com/unsub>"},
            ],
            "body": {"data": ""},
        },
    }

    email = parse_message(resource)

    assert email.unsubscribe_url == "https://example.com/unsub"


def test_parse_message_without_list_unsubscribe():
    """Test that parse_message sets unsubscribe_url to None when header missing."""
    resource = {
        "id": "test_123",
        "threadId": "thread_456",
        "snippet": "Test email",
        "labelIds": [],
        "payload": {
            "headers": [
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Test"},
                {"name": "Date", "value": "Mon, 01 Jan 2024 12:00:00 +0000"},
            ],
            "body": {"data": ""},
        },
    }

    email = parse_message(resource)

    assert email.unsubscribe_url is None


def test_parse_message_unsubscribe_url_with_mailto():
    """Test parse_message with mailto unsubscribe URL."""
    resource = {
        "id": "test_123",
        "threadId": "thread_456",
        "snippet": "Test email",
        "labelIds": [],
        "payload": {
            "headers": [
                {"name": "From", "value": "newsletter@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Newsletter"},
                {"name": "Date", "value": "Mon, 01 Jan 2024 12:00:00 +0000"},
                {"name": "List-Unsubscribe", "value": "<mailto:unsub@example.com>"},
            ],
            "body": {"data": ""},
        },
    }

    email = parse_message(resource)

    assert email.unsubscribe_url == "mailto:unsub@example.com"


def test_extract_unsubscribe_url_with_query_params():
    """Test extraction of URL with query parameters."""
    header = "<https://example.com/unsub?token=abc123&email=user%40example.com>"
    result = _extract_unsubscribe_url(header)
    assert result == "https://example.com/unsub?token=abc123&email=user%40example.com"
