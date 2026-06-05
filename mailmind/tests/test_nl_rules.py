"""Tests for natural language rule parser (nl_rules.py).

All tests mock the DeepSeek LLM client to avoid real API calls.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mailmind.intelligence.nl_rules import parse_rule_nl


class FakeChoice:
    """Simulates a chat completion choice."""
    def __init__(self, content: str):
        self.message = MagicMock()
        self.message.content = content


class FakeResponse:
    """Simulates a chat completion response."""
    def __init__(self, content: str):
        self.choices = [FakeChoice(content)]


@pytest.fixture
def mock_client():
    """Create a mocked DeepSeekClient."""
    client = MagicMock()
    client.model = "deepseek-chat"
    client.client = MagicMock()
    return client


def test_parse_rule_nl_valid_sentence(mock_client):
    """Test parsing a valid rule sentence."""
    mock_client.client.chat.completions.create.return_value = FakeResponse(
        json.dumps({
            "sender_email": "billing@acme.com",
            "label": "FINANCE",
            "unsupported": False,
            "unsupported_reason": None,
        })
    )

    result = parse_rule_nl(
        "label anything from billing@acme.com as FINANCE",
        mock_client
    )

    assert result["error"] is None
    assert result["sender_email"] == "billing@acme.com"
    assert result["label"] == "FINANCE"
    assert result["unsupported"] is False


def test_parse_rule_nl_topic_scoped(mock_client):
    """A topic-scoped rule returns a match_pattern for subject filtering."""
    mock_client.client.chat.completions.create.return_value = FakeResponse(
        json.dumps({
            "sender_email": "oe-l@cserkesz.hu",
            "label": "CALENDAR",
            "match_pattern": "esemény|meghívó|event|invite",
            "unsupported": False,
            "unsupported_reason": None,
        })
    )

    result = parse_rule_nl(
        "label emails from oe-l@cserkesz.hu about events as CALENDAR",
        mock_client
    )

    assert result["error"] is None
    assert result["sender_email"] == "oe-l@cserkesz.hu"
    assert result["label"] == "CALENDAR"
    assert result["match_pattern"] == "esemény|meghívó|event|invite"


def test_parse_rule_nl_catch_all_pattern_is_none(mock_client):
    """A catch-all rule (no topic) has match_pattern None on the success path."""
    mock_client.client.chat.completions.create.return_value = FakeResponse(
        json.dumps({
            "sender_email": "billing@acme.com",
            "label": "FINANCE",
            "match_pattern": None,
            "unsupported": False,
            "unsupported_reason": None,
        })
    )

    result = parse_rule_nl("label billing@acme.com as FINANCE", mock_client)

    assert result["error"] is None
    assert result["match_pattern"] is None


def test_parse_rule_nl_unknown_label(mock_client):
    """Test that unknown labels are rejected."""
    mock_client.client.chat.completions.create.return_value = FakeResponse(
        json.dumps({
            "sender_email": "test@example.com",
            "label": "UNKNOWN_LABEL",
            "unsupported": False,
            "unsupported_reason": None,
        })
    )

    result = parse_rule_nl(
        "label test@example.com as UNKNOWN_LABEL",
        mock_client
    )

    assert result["error"] is not None
    assert "Unknown label" in result["error"]
    assert result["label"] is None


def test_parse_rule_nl_unsupported_clause(mock_client):
    """Test that unsupported clauses are reported."""
    mock_client.client.chat.completions.create.return_value = FakeResponse(
        json.dumps({
            "sender_email": "news@example.com",
            "label": "NEWSLETTER",
            "unsupported": True,
            "unsupported_reason": "never archive action not supported",
        })
    )

    result = parse_rule_nl(
        "label news@example.com as NEWSLETTER and never archive",
        mock_client
    )

    assert result["error"] is not None
    assert "unsupported action" in result["error"].lower()
    assert result["unsupported"] is True


def test_parse_rule_nl_no_sender_found(mock_client):
    """Test error when sender email cannot be extracted."""
    mock_client.client.chat.completions.create.return_value = FakeResponse(
        json.dumps({
            "sender_email": None,
            "label": "WORK",
            "unsupported": False,
            "unsupported_reason": None,
        })
    )

    result = parse_rule_nl(
        "create a work rule",
        mock_client
    )

    assert result["error"] is not None
    assert "sender email" in result["error"].lower()


def test_parse_rule_nl_no_label_found(mock_client):
    """Test error when label cannot be extracted."""
    mock_client.client.chat.completions.create.return_value = FakeResponse(
        json.dumps({
            "sender_email": "test@example.com",
            "label": None,
            "unsupported": False,
            "unsupported_reason": None,
        })
    )

    result = parse_rule_nl(
        "create a rule for test@example.com",
        mock_client
    )

    assert result["error"] is not None
    assert "label" in result["error"].lower()


def test_parse_rule_nl_empty_input():
    """Test that empty input returns error."""
    mock_client = MagicMock()
    result = parse_rule_nl("", mock_client)

    assert result["error"] is not None
    assert result["sender_email"] is None
    assert result["label"] is None


def test_parse_rule_nl_whitespace_only():
    """Test that whitespace-only input returns error."""
    mock_client = MagicMock()
    result = parse_rule_nl("   ", mock_client)

    assert result["error"] is not None
    assert result["sender_email"] is None


def test_parse_rule_nl_malformed_json(mock_client):
    """Test handling of malformed JSON from LLM."""
    mock_client.client.chat.completions.create.return_value = FakeResponse(
        "this is not json"
    )

    result = parse_rule_nl(
        "label test@example.com as FINANCE",
        mock_client
    )

    assert result["error"] is not None
    assert "Failed to parse" in result["error"]


def test_parse_rule_nl_empty_llm_response(mock_client):
    """Test handling of empty LLM response."""
    mock_client.client.chat.completions.create.return_value = FakeResponse("")

    result = parse_rule_nl(
        "label test@example.com as FINANCE",
        mock_client
    )

    assert result["error"] is not None
    assert "empty response" in result["error"].lower()


def test_parse_rule_nl_case_insensitive_label(mock_client):
    """Test that labels are case-insensitive."""
    mock_client.client.chat.completions.create.return_value = FakeResponse(
        json.dumps({
            "sender_email": "newsletter@example.com",
            "label": "newsletter",  # lowercase
            "unsupported": False,
            "unsupported_reason": None,
        })
    )

    result = parse_rule_nl(
        "label newsletter@example.com as NEWSLETTER",
        mock_client
    )

    assert result["error"] is None
    assert result["label"] == "NEWSLETTER"  # normalized to uppercase
