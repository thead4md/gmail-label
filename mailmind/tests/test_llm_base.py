"""Tests for the unified LLM classifier Protocol.

Verifies that both DeepSeekClient and OpenAIAdapter conform to the
LLMClassifier Protocol and return LLMResult on mocked responses.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mailmind.llm.base import LLMClassifier, LLMResult
from mailmind.llm.deepseek import DeepSeekClient
from mailmind.ml.llm_classifier import LLMClassifier as OpenAIClassifier, OpenAIAdapter
from mailmind.storage.models import Email
from mailmind.config import MailMindConfig


class FakeChoice:
    """Simulates a chat completion choice."""
    def __init__(self, content: str):
        self.message = MagicMock()
        self.message.content = content


class FakeResponse:
    """Simulates a chat completion response."""
    def __init__(self, content: str):
        self.choices = [FakeChoice(content)]


class TestLLMResultType:
    """Tests for LLMResult dataclass."""

    def test_llm_result_defaults(self):
        """Test that LLMResult has sensible defaults."""
        result = LLMResult()
        assert result.primary_label == "NOTIFICATION"
        assert result.llm_confidence == 0.0
        assert result.reasoning == ""
        assert result.model_available is True

    def test_llm_result_to_scoring_breakdown(self):
        """Test LLMResult conversion to scoring breakdown."""
        result = LLMResult(
            primary_label="PERSONAL",
            llm_confidence=0.85,
            reasoning="Directly addressed.",
            model_available=True,
        )
        breakdown = result.to_scoring_breakdown_entry()
        assert breakdown == {
            "label": "PERSONAL",
            "confidence": 0.85,
            "reasoning": "Directly addressed.",
        }


class TestProtocolConformance:
    """Tests that both clients conform to the LLMClassifier Protocol."""

    def test_deepseek_client_has_classify_email_method(self):
        """Verify DeepSeekClient implements classify_email()."""
        config = MailMindConfig(
            deepseek_api_key="test-key",
            deepseek_model="deepseek-chat",
        )
        with patch("mailmind.llm.deepseek.OpenAI"):
            client = DeepSeekClient(config)
            assert hasattr(client, "classify_email")
            assert callable(client.classify_email)

    def test_openai_adapter_has_classify_email_method(self):
        """Verify OpenAIAdapter implements classify_email()."""
        classifier = MagicMock(spec=OpenAIClassifier)
        adapter = OpenAIAdapter(classifier)
        assert hasattr(adapter, "classify_email")
        assert callable(adapter.classify_email)

    def test_deepseek_classify_email_signature(self):
        """Test DeepSeekClient.classify_email() has correct signature."""
        config = MailMindConfig(deepseek_api_key="test-key")
        with patch("mailmind.llm.deepseek.OpenAI"):
            client = DeepSeekClient(config)
            email = Email(
                gmail_id="test123",
                subject="Test",
                sender="test@example.com",
                body_text="Test body",
            )
            # Check that classify_email accepts an Email object
            import inspect
            sig = inspect.signature(client.classify_email)
            assert "email" in sig.parameters
            assert sig.parameters["email"].annotation in (Email, "Email")

    def test_openai_adapter_classify_email_signature(self):
        """Test OpenAIAdapter.classify_email() has correct signature."""
        classifier = MagicMock(spec=OpenAIClassifier)
        adapter = OpenAIAdapter(classifier)
        email = Email(
            gmail_id="test123",
            subject="Test",
            sender="test@example.com",
            body_text="Test body",
        )
        import inspect
        sig = inspect.signature(adapter.classify_email)
        assert "email" in sig.parameters


class TestDeepSeekClientProtocol:
    """Tests for DeepSeekClient conformance to Protocol."""

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_deepseek_returns_llm_result(self, mock_openai):
        """Test DeepSeekClient.classify_email() returns LLMResult."""
        config = MailMindConfig(
            deepseek_api_key="test-key",
            deepseek_model="deepseek-chat",
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = FakeResponse(
            json.dumps({
                "label": "PERSONAL",
                "confidence": 0.92,
                "reasoning": "Personal email",
            })
        )
        mock_openai.return_value = mock_client

        client = DeepSeekClient(config)

        email = Email(
            gmail_id="test123",
            subject="Test",
            sender="test@example.com",
            body_text="Test body",
        )

        result = client.classify_email(email)

        assert isinstance(result, LLMResult)
        assert result.primary_label == "PERSONAL"
        assert result.llm_confidence == 0.92
        assert result.reasoning == "Personal email"
        assert result.model_available is True

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_deepseek_handles_api_failure(self, mock_openai):
        """Test DeepSeekClient returns LLMResult on API failure."""
        config = MailMindConfig(deepseek_api_key="test-key")
        client = DeepSeekClient(config)

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")
        mock_openai.return_value = mock_client

        email = Email(
            gmail_id="test123",
            subject="Test",
            sender="test@example.com",
            body_text="Test body",
        )

        result = client.classify_email(email)

        assert isinstance(result, LLMResult)
        assert result.model_available is False


class TestOpenAIAdapterProtocol:
    """Tests for OpenAIAdapter conformance to Protocol."""

    def test_openai_adapter_converts_prediction_to_result(self):
        """Test OpenAIAdapter converts LLMPrediction to LLMResult."""
        from mailmind.ml.llm_classifier import LLMPrediction

        classifier = MagicMock(spec=OpenAIClassifier)
        prediction = LLMPrediction(
            label="NEWSLETTER",
            confidence=0.78,
            rationale="Contains unsubscribe link.",
            action_hint="Archive",
            needs_review=False,
        )
        classifier.classify.return_value = prediction

        adapter = OpenAIAdapter(classifier)
        email = Email(
            gmail_id="test123",
            subject="Weekly Update",
            sender="newsletter@example.com",
            body_text="",
        )

        result = adapter.classify_email(email)

        assert isinstance(result, LLMResult)
        assert result.primary_label == "NEWSLETTER"
        assert result.llm_confidence == 0.78
        assert result.reasoning == "Contains unsubscribe link."
        assert result.model_available is True

        # Verify it called the underlying classifier with extracted fields
        classifier.classify.assert_called_once_with(
            sender="newsletter@example.com",
            subject="Weekly Update",
            snippet="",
            body_text="",
            gmail_id="test123",
        )

    def test_openai_adapter_handles_failed_classification(self):
        """Test OpenAIAdapter returns LLMResult on classification failure."""
        classifier = MagicMock(spec=OpenAIClassifier)
        classifier.classify.return_value = None

        adapter = OpenAIAdapter(classifier)
        email = Email(
            gmail_id="test123",
            subject="Test",
            sender="test@example.com",
            body_text="",
        )

        result = adapter.classify_email(email)

        assert isinstance(result, LLMResult)
        assert result.model_available is False

    def test_openai_adapter_handles_none_fields(self):
        """Test OpenAIAdapter handles None email fields gracefully."""
        from mailmind.ml.llm_classifier import LLMPrediction

        classifier = MagicMock(spec=OpenAIClassifier)
        classifier.classify.return_value = LLMPrediction(
            label="OTHER",
            confidence=0.5,
            rationale="Unknown.",
        )

        adapter = OpenAIAdapter(classifier)
        email = Email(
            gmail_id="test123",
            subject=None,
            sender=None,
            body_text=None,
            snippet=None,
        )

        result = adapter.classify_email(email)

        assert isinstance(result, LLMResult)
        # Verify None fields are converted to empty strings
        classifier.classify.assert_called_once_with(
            sender="",
            subject="",
            snippet="",
            body_text="",
            gmail_id="test123",
        )


class TestProtocolUsage:
    """Test that the Protocol can be used as a type for both clients."""

    @patch("mailmind.llm.deepseek.OpenAI")
    def test_deepseek_satisfies_protocol(self, mock_openai):
        """Test DeepSeekClient satisfies the LLMClassifier Protocol."""
        config = MailMindConfig(deepseek_api_key="test-key")

        # Mock the API
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = FakeResponse(
            json.dumps({
                "label": "NOTIFICATION",
                "confidence": 0.80,
                "reasoning": "System notification",
            })
        )
        mock_openai.return_value = mock_client

        client = DeepSeekClient(config)

        # Use it through the Protocol type
        llm: LLMClassifier = client
        email = Email(
            gmail_id="test123",
            subject="Notification",
            sender="system@example.com",
            body_text="Your account is verified.",
        )
        result = llm.classify_email(email)

        assert isinstance(result, LLMResult)
        assert result.model_available is True

    def test_openai_adapter_satisfies_protocol(self):
        """Test OpenAIAdapter satisfies the LLMClassifier Protocol."""
        from mailmind.ml.llm_classifier import LLMPrediction

        classifier = MagicMock(spec=OpenAIClassifier)
        classifier.classify.return_value = LLMPrediction(
            label="PERSONAL",
            confidence=0.90,
            rationale="Personal message.",
        )

        adapter = OpenAIAdapter(classifier)

        # Use it through the Protocol type
        llm: LLMClassifier = adapter
        email = Email(
            gmail_id="test123",
            subject="Hello",
            sender="friend@example.com",
            body_text="How are you?",
        )
        result = llm.classify_email(email)

        assert isinstance(result, LLMResult)
        assert result.model_available is True
