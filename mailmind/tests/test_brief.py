"""Tests for MailMind daily brief generation.

All tests use mocked LLM client and database calls — no real API calls.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestDailyBrief:
    """Tests for build_daily_brief()."""

    def test_daily_brief_with_llm_client_returns_summary(self):
        """Test build_daily_brief returns summary when LLM client available."""
        from mailmind.intelligence.brief import build_daily_brief

        mock_db = MagicMock()
        mock_llm_client = MagicMock()

        # Mock database query results
        mock_db.execute_sql.return_value.fetchall.return_value = [
            ("PERSONAL", 85, "Meet tomorrow?", "alice@example.com"),
            ("FINANCE", 80, "Invoice approval needed", "finance@example.com"),
        ]

        # Mock LLM response
        mock_llm_client.model = "deepseek-chat"
        mock_llm_client.client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            "- Confirm meeting with Alice\n"
            "- Approve invoice\n"
            "- Reply to pending questions"
        )
        mock_llm_client.client.chat.completions.create.return_value = mock_response

        result = build_daily_brief(mock_db, account="user@example.com", llm_client=mock_llm_client)

        assert isinstance(result, str)
        assert len(result) > 0
        assert "Confirm meeting" in result or "Approve invoice" in result

    def test_daily_brief_without_llm_client_returns_empty(self):
        """Test build_daily_brief returns empty string when LLM client is None."""
        from mailmind.intelligence.brief import build_daily_brief

        mock_db = MagicMock()
        result = build_daily_brief(mock_db, account="user@example.com", llm_client=None)

        assert result == ""

    def test_daily_brief_no_items_returns_empty(self):
        """Test build_daily_brief returns empty string when no items found."""
        from mailmind.intelligence.brief import build_daily_brief

        mock_db = MagicMock()
        mock_db.execute_sql.return_value.fetchall.return_value = []

        mock_llm_client = MagicMock()
        result = build_daily_brief(mock_db, account="user@example.com", llm_client=mock_llm_client)

        assert result == ""

    def test_daily_brief_llm_error_returns_empty(self):
        """Test build_daily_brief returns empty string on LLM error."""
        from mailmind.intelligence.brief import build_daily_brief

        mock_db = MagicMock()
        mock_db.execute_sql.return_value.fetchall.return_value = [
            ("PERSONAL", 85, "Meet tomorrow?", "alice@example.com"),
        ]

        mock_llm_client = MagicMock()
        mock_llm_client.model = "deepseek-chat"
        mock_llm_client.client = MagicMock()
        mock_llm_client.client.chat.completions.create.side_effect = Exception("API error")

        result = build_daily_brief(mock_db, account="user@example.com", llm_client=mock_llm_client)

        assert result == ""

    def test_daily_brief_caps_output_at_200_chars(self):
        """Test build_daily_brief caps output to 200 characters."""
        from mailmind.intelligence.brief import build_daily_brief

        mock_db = MagicMock()
        mock_db.execute_sql.return_value.fetchall.return_value = [
            ("PERSONAL", 85, "Meet tomorrow?", "alice@example.com"),
        ]

        mock_llm_client = MagicMock()
        mock_llm_client.model = "deepseek-chat"
        mock_llm_client.client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        long_output = "A" * 300  # 300 chars, should be capped to 200
        mock_response.choices[0].message.content = long_output
        mock_llm_client.client.chat.completions.create.return_value = mock_response

        result = build_daily_brief(mock_db, account="user@example.com", llm_client=mock_llm_client)

        assert len(result) <= 200
        assert result == "A" * 200
