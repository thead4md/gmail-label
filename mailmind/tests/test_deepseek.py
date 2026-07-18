"""Tests for DeepSeek LLM client.

All tests use mocked API calls — no real DeepSeek API calls are made.
The openai client is patched at the module level.

Covers:
- Successful classification with valid JSON response
- Invalid label returned by LLM
- Malformed JSON response
- API timeout exception
- API connection error
- Generic API error
- Empty response (no content)
- LLMResult default values
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from mailmind.storage.models import Email


def _make_test_email(
    gmail_id: str = "llm_test_001",
    sender: str = "alice@example.com",
    subject: str = "Hey, how are you?",
    body_text: str = "Hi friend, just checking in. Let's grab coffee this weekend!",
) -> Email:
    return Email(
        gmail_id=gmail_id,
        sender=sender,
        subject=subject,
        snippet="",
        body_text=body_text,
        recipients=["me@example.com"],
        date_ts=int(datetime.now(timezone.utc).timestamp()),
        labels=[],
        parsed=True,
    )


class FakeChoice:
    """Simulates a chat completion choice."""
    def __init__(self, content: str):
        self.message = MagicMock()
        self.message.content = content


class FakeResponse:
    """Simulates a chat completion response."""
    def __init__(self, content: str):
        self.choices = [FakeChoice(content)]


class TestDeepSeekClient:
    """Tests for DeepSeekClient.classify_email()."""

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_classify_email_success(self, mock_openai_class):
        """Test successful classification returns correct LLMResult."""
        # Arrange
        mock_instance = MagicMock()
        mock_openai_class.return_value = mock_instance

        expected_label = "PERSONAL"
        expected_confidence = 0.93
        expected_reasoning = "Directly addressed personal email."

        mock_instance.chat.completions.create.return_value = FakeResponse(
            json.dumps({
                "label": expected_label,
                "confidence": expected_confidence,
                "reasoning": expected_reasoning,
            })
        )

        from mailmind.config import MailMindConfig
        from mailmind.llm.base import LLMResult
        config = MailMindConfig(
            deepseek_api_key="sk-test-key",
            llm_enabled=True,
        )
        client = __import__("mailmind.llm.deepseek", fromlist=["DeepSeekClient"]).DeepSeekClient(config)
        email = _make_test_email()

        # Act
        result = client.classify_email(email)

        # Assert
        assert result.model_available is True
        assert result.primary_label == expected_label
        assert result.llm_confidence == expected_confidence
        assert result.reasoning == expected_reasoning

        # Verify API was called with correct parameters
        call_kwargs = mock_instance.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == config.deepseek_model
        assert call_kwargs["response_format"] == {"type": "json_object"}
        assert call_kwargs["temperature"] == 0.1
        assert len(call_kwargs["messages"]) == 2
        assert call_kwargs["messages"][0]["role"] == "system"
        assert call_kwargs["messages"][1]["role"] == "user"

    @pytest.mark.parametrize("label", ["WORK", "URGENT"])
    @patch("mailmind.llm.deepseek.OpenAI")
    def test_classify_email_accepts_work_and_urgent(self, mock_openai_class, label):
        """Regression test for FIX 2: WORK and URGENT are the two highest
        base-score labels in taxonomy.py (60 and 80) but were missing from
        DEEPSEEK_LABELS/SYSTEM_PROMPT, so any work/urgent email routed to
        this tier was structurally forced into the closest available
        category (almost always NOTIFICATION). Both must now validate."""
        mock_instance = MagicMock()
        mock_openai_class.return_value = mock_instance

        mock_instance.chat.completions.create.return_value = FakeResponse(
            json.dumps({
                "label": label,
                "confidence": 0.88,
                "reasoning": "Business-critical content.",
            })
        )

        from mailmind.config import MailMindConfig
        config = MailMindConfig(deepseek_api_key="sk-test-key", llm_enabled=True)
        client = __import__("mailmind.llm.deepseek", fromlist=["DeepSeekClient"]).DeepSeekClient(config)
        email = _make_test_email()

        result = client.classify_email(email)

        assert result.model_available is True
        assert result.primary_label == label

    def test_valid_labels_include_work_and_urgent(self):
        """DEEPSEEK_LABELS (imported from taxonomy.py) must include WORK and
        URGENT, the two highest-priority labels in the full taxonomy."""
        from mailmind.llm.deepseek import DeepSeekClient

        assert "WORK" in DeepSeekClient.VALID_LABELS
        assert "URGENT" in DeepSeekClient.VALID_LABELS

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_classify_email_invalid_label(self, mock_openai_class):
        """Test invalid label returns model_available=False."""
        mock_instance = MagicMock()
        mock_openai_class.return_value = mock_instance

        mock_instance.chat.completions.create.return_value = FakeResponse(
            json.dumps({
                "label": "INVALID_LABEL",
                "confidence": 0.99,
                "reasoning": "Some reasoning",
            })
        )

        from mailmind.config import MailMindConfig
        config = MailMindConfig(deepseek_api_key="sk-test-key", llm_enabled=True)
        client = __import__("mailmind.llm.deepseek", fromlist=["DeepSeekClient"]).DeepSeekClient(config)
        email = _make_test_email()

        result = client.classify_email(email)

        assert result.model_available is False
        assert result.llm_confidence == 0.0
        assert "Invalid label" in result.reasoning

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_classify_email_malformed_json(self, mock_openai_class):
        """Test malformed JSON returns model_available=False."""
        mock_instance = MagicMock()
        mock_openai_class.return_value = mock_instance

        mock_instance.chat.completions.create.return_value = FakeResponse(
            "{this is not valid json}"
        )

        from mailmind.config import MailMindConfig
        config = MailMindConfig(deepseek_api_key="sk-test-key", llm_enabled=True)
        client = __import__("mailmind.llm.deepseek", fromlist=["DeepSeekClient"]).DeepSeekClient(config)
        email = _make_test_email()

        result = client.classify_email(email)

        assert result.model_available is False
        assert result.llm_confidence == 0.0
        assert "Malformed JSON" in result.reasoning

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_classify_email_timeout(self, mock_openai_class):
        """Test timeout exception returns graceful fallback."""
        from openai import APITimeoutError

        mock_instance = MagicMock()
        mock_openai_class.return_value = mock_instance

        mock_instance.chat.completions.create.side_effect = APITimeoutError(
            "Request timed out"
        )

        from mailmind.config import MailMindConfig
        config = MailMindConfig(deepseek_api_key="sk-test-key", llm_enabled=True)
        client = __import__("mailmind.llm.deepseek", fromlist=["DeepSeekClient"]).DeepSeekClient(config)
        email = _make_test_email()

        result = client.classify_email(email)

        assert result.model_available is False
        assert result.llm_confidence == 0.0
        assert "timeout" in result.reasoning.lower()

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_classify_email_connection_error(self, mock_openai_class):
        """Test connection error returns graceful fallback."""
        from openai import APIConnectionError

        mock_instance = MagicMock()
        mock_openai_class.return_value = mock_instance

        mock_instance.chat.completions.create.side_effect = APIConnectionError(
            message="Connection failed",
            request=MagicMock(),
        )

        from mailmind.config import MailMindConfig
        config = MailMindConfig(deepseek_api_key="sk-test-key", llm_enabled=True)
        client = __import__("mailmind.llm.deepseek", fromlist=["DeepSeekClient"]).DeepSeekClient(config)
        email = _make_test_email()

        result = client.classify_email(email)

        assert result.model_available is False
        assert result.llm_confidence == 0.0
        assert "connection" in result.reasoning.lower()

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_classify_email_api_error(self, mock_openai_class):
        """Test API error returns graceful fallback."""
        from openai import APIError

        mock_instance = MagicMock()
        mock_openai_class.return_value = mock_instance

        mock_instance.chat.completions.create.side_effect = APIError(
            message="Rate limit exceeded",
            request=MagicMock(),
            body={"error": "rate_limit"},
        )

        from mailmind.config import MailMindConfig
        config = MailMindConfig(deepseek_api_key="sk-test-key", llm_enabled=True)
        client = __import__("mailmind.llm.deepseek", fromlist=["DeepSeekClient"]).DeepSeekClient(config)
        email = _make_test_email()

        result = client.classify_email(email)

        assert result.model_available is False
        assert result.llm_confidence == 0.0

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_classify_email_empty_response(self, mock_openai_class):
        """Test empty content returns fallback."""
        mock_instance = MagicMock()
        mock_openai_class.return_value = mock_instance

        mock_instance.chat.completions.create.return_value = FakeResponse(
            None
        )

        from mailmind.config import MailMindConfig
        config = MailMindConfig(deepseek_api_key="sk-test-key", llm_enabled=True)
        client = __import__("mailmind.llm.deepseek", fromlist=["DeepSeekClient"]).DeepSeekClient(config)
        email = _make_test_email()

        result = client.classify_email(email)

        assert result.model_available is False
        assert result.llm_confidence == 0.0
        assert "empty" in result.reasoning.lower()

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_prompt_contains_no_full_body(self, mock_openai_class):
        """Verify prompt never includes full body_text — only first 500 chars."""
        mock_instance = MagicMock()
        mock_openai_class.return_value = mock_instance

        mock_instance.chat.completions.create.return_value = FakeResponse(
            json.dumps({
                "label": "NOTIFICATION",
                "confidence": 0.5,
                "reasoning": "Default",
            })
        )

        # Create email with body longer than 500 chars
        long_body = "A" * 1000
        email = _make_test_email(body_text=long_body)

        from mailmind.config import MailMindConfig
        config = MailMindConfig(deepseek_api_key="sk-test-key", llm_enabled=True)
        client = __import__("mailmind.llm.deepseek", fromlist=["DeepSeekClient"]).DeepSeekClient(config)
        client.classify_email(email)

        call_kwargs = mock_instance.chat.completions.create.call_args[1]
        user_content = call_kwargs["messages"][1]["content"]

        # Body in prompt should be truncated to 500 chars
        body_marker = "Body: "
        body_start = user_content.index(body_marker) + len(body_marker)
        body_in_prompt = user_content[body_start:]
        assert len(body_in_prompt) <= 500, f"Body too long: {len(body_in_prompt)} chars"
        assert len(body_in_prompt) < 600  # Body truncated
        assert len(user_content) < 800  # Total prompt should be reasonable


    @patch("mailmind.llm.deepseek.OpenAI")
    def test_summarize_thread_success(self, mock_openai_class):
        """Test successful thread summarization returns text."""
        mock_instance = MagicMock()
        mock_openai_class.return_value = mock_instance

        expected_summary = "User is asking for help with email filtering."

        mock_instance.chat.completions.create.return_value = FakeResponse(
            expected_summary
        )

        from mailmind.config import MailMindConfig
        config = MailMindConfig(deepseek_api_key="sk-test-key", llm_enabled=True)
        client = __import__("mailmind.llm.deepseek", fromlist=["DeepSeekClient"]).DeepSeekClient(config)

        result = client.summarize_thread(
            subject="Help with filtering",
            body_text="Hi, I need help setting up email filters. Can you assist?",
        )

        assert result == expected_summary
        # Verify API was called with correct parameters
        call_kwargs = mock_instance.chat.completions.create.call_args[1]
        assert call_kwargs["temperature"] == 0.1
        assert call_kwargs["max_tokens"] == 80

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_summarize_thread_error_returns_empty(self, mock_openai_class):
        """Test summarize_thread returns empty string on API error."""
        from openai import APITimeoutError

        mock_instance = MagicMock()
        mock_openai_class.return_value = mock_instance

        mock_instance.chat.completions.create.side_effect = APITimeoutError(
            "Request timed out"
        )

        from mailmind.config import MailMindConfig
        config = MailMindConfig(deepseek_api_key="sk-test-key", llm_enabled=True)
        client = __import__("mailmind.llm.deepseek", fromlist=["DeepSeekClient"]).DeepSeekClient(config)

        result = client.summarize_thread(
            subject="Test",
            body_text="Test body",
        )

        assert result == ""

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_summarize_thread_caps_output_at_120_chars(self, mock_openai_class):
        """Test summarize_thread caps output to 120 characters."""
        mock_instance = MagicMock()
        mock_openai_class.return_value = mock_instance

        long_summary = "A" * 200  # 200 chars, should be capped to 120

        mock_instance.chat.completions.create.return_value = FakeResponse(
            long_summary
        )

        from mailmind.config import MailMindConfig
        config = MailMindConfig(deepseek_api_key="sk-test-key", llm_enabled=True)
        client = __import__("mailmind.llm.deepseek", fromlist=["DeepSeekClient"]).DeepSeekClient(config)

        result = client.summarize_thread(
            subject="Test",
            body_text="Test body",
        )

        assert len(result) <= 120
        assert result == "A" * 120

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_summarize_thread_empty_response(self, mock_openai_class):
        """Test summarize_thread returns empty string on empty response."""
        mock_instance = MagicMock()
        mock_openai_class.return_value = mock_instance

        mock_instance.chat.completions.create.return_value = FakeResponse(None)

        from mailmind.config import MailMindConfig
        config = MailMindConfig(deepseek_api_key="sk-test-key", llm_enabled=True)
        client = __import__("mailmind.llm.deepseek", fromlist=["DeepSeekClient"]).DeepSeekClient(config)

        result = client.summarize_thread(
            subject="Test",
            body_text="Test body",
        )

        assert result == ""


class TestLLMResult:
    """Tests for LLMResult dataclass."""

    def test_default_values(self):
        """Test LLMResult default constructor values."""
        from mailmind.llm.base import LLMResult

        result = LLMResult()
        assert result.primary_label == "NOTIFICATION"
        assert result.llm_confidence == 0.0
        assert result.reasoning == ""
        assert result.model_available is True

    def test_to_scoring_breakdown_entry(self):
        """Test conversion to scoring breakdown dict."""
        from mailmind.llm.base import LLMResult

        result = LLMResult(
            primary_label="WORK",
            llm_confidence=0.85,
            reasoning="Business-related email from colleague.",
        )
        entry = result.to_scoring_breakdown_entry()
        assert entry["label"] == "WORK"
        assert entry["confidence"] == 0.85
        assert "Business-related" in entry["reasoning"]
