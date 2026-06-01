"""Unit tests for mailmind/ml/llm_classifier.py.

Tests the LLMClassifier without making actual API calls (mocks openai).
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

from mailmind.ml.llm_classifier import LLMClassifier, LLMPrediction, VALID_LABELS


def _make_mock_openai_response(content: str) -> MagicMock:
    """Create a mock OpenAI chat completion response."""
    mock_choice = MagicMock()
    mock_choice.message.content = content
    
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    return mock_response


class TestLLMClassifier:
    """Tests for LLMClassifier class."""

    def _make_classifier(self) -> LLMClassifier:
        return LLMClassifier(api_key="sk-test-key", model="gpt-4o-mini")

    @patch("openai.OpenAI")
    def test_valid_response_parsed_correctly(self, mock_openai):
        """Test that a valid JSON response is parsed into LLMPrediction correctly."""
        classifier = self._make_classifier()
        
        valid_json = json.dumps({
            "label": "NEWSLETTER",
            "confidence": 0.87,
            "rationale": "Contains unsubscribe link in body.",
            "action_hint": "Archive after reading",
            "needs_review": False,
        })
        
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_openai_response(valid_json)
        mock_openai.return_value = mock_client
        
        result = classifier.classify(
            sender="newsletter@example.com",
            subject="Your Weekly Update",
            snippet="Here are this week's top stories",
            body_text="Click here to unsubscribe",
            gmail_id="test123",
        )
        
        assert result is not None
        assert isinstance(result, LLMPrediction)
        assert result.label == "NEWSLETTER"
        assert result.confidence == 0.87
        assert result.rationale == "Contains unsubscribe link in body."
        assert result.action_hint == "Archive after reading"
        assert result.needs_review is False

    @patch("openai.OpenAI")
    def test_valid_response_needs_review_labels(self, mock_openai):
        """Test that PERSONAL, ACTION_REQUIRED, FINANCE, MEETING always get needs_review=True."""
        classifier = self._make_classifier()
        
        for review_label in ["PERSONAL", "ACTION_REQUIRED", "FINANCE", "MEETING"]:
            valid_json = json.dumps({
                "label": review_label,
                "confidence": 0.75,
                "rationale": "Important email.",
                "action_hint": None,
                "needs_review": False,  # LLM says False, but rule overrides
            })
            
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_openai_response(valid_json)
            mock_openai.return_value = mock_client
            
            result = classifier.classify(
                sender="person@example.com",
                subject="Test",
                snippet="",
                body_text="",
                gmail_id=f"test_{review_label}",
            )
            
            assert result is not None, f"Failed for label {review_label}"
            assert result.needs_review is True, f"needs_review should be True for {review_label}"

    @patch("openai.OpenAI")
    def test_invalid_label_returns_none(self, mock_openai):
        """Test that an unknown label causes classify() to return None."""
        classifier = self._make_classifier()
        
        invalid_json = json.dumps({
            "label": "INVALID_LABEL_123",
            "confidence": 0.90,
            "rationale": "Some reason.",
            "action_hint": None,
            "needs_review": False,
        })
        
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_openai_response(invalid_json)
        mock_openai.return_value = mock_client
        
        result = classifier.classify(
            sender="test@example.com",
            subject="Test",
            snippet="",
            body_text="",
            gmail_id="test_invalid",
        )
        
        assert result is None

    @patch("openai.OpenAI")
    def test_api_failure_returns_none(self, mock_openai):
        """Test that an API exception causes classify() to return None without raising."""
        classifier = self._make_classifier()
        
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API timeout")
        mock_openai.return_value = mock_client
        
        result = classifier.classify(
            sender="test@example.com",
            subject="Test",
            snippet="",
            body_text="",
            gmail_id="test_api_fail",
        )
        
        assert result is None

    @patch("openai.OpenAI")
    def test_confidence_clamped_if_out_of_range(self, mock_openai):
        """Test that confidence > 1.0 is clamped to 1.0, and < 0.0 is clamped to 0.0."""
        classifier = self._make_classifier()
        
        # Test over-range confidence
        over_json = json.dumps({
            "label": "NOTIFICATION",
            "confidence": 1.5,
            "rationale": "Over-confident.",
            "action_hint": None,
            "needs_review": False,
        })
        
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_openai_response(over_json)
        mock_openai.return_value = mock_client
        
        result = classifier.classify(
            sender="test@example.com",
            subject="Test",
            snippet="",
            body_text="",
            gmail_id="test_over",
        )
        
        assert result is not None
        assert result.confidence == 1.0, f"Expected 1.0, got {result.confidence}"
        
        # Test under-range confidence
        under_json = json.dumps({
            "label": "NOTIFICATION",
            "confidence": -0.5,
            "rationale": "Under-confident.",
            "action_hint": None,
            "needs_review": False,
        })
        
        mock_client.chat.completions.create.return_value = _make_mock_openai_response(under_json)
        mock_openai.return_value = mock_client
        
        result = classifier.classify(
            sender="test@example.com",
            subject="Test",
            snippet="",
            body_text="",
            gmail_id="test_under",
        )
        
        assert result is not None
        assert result.confidence == 0.0, f"Expected 0.0, got {result.confidence}"

    @patch("openai.OpenAI")
    def test_empty_rationale_uses_default(self, mock_openai):
        """Test that empty rationale is replaced with default string."""
        classifier = self._make_classifier()
        
        empty_rationale_json = json.dumps({
            "label": "SPAM",
            "confidence": 0.99,
            "rationale": "",
            "action_hint": "Delete",
            "needs_review": False,
        })
        
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_openai_response(empty_rationale_json)
        mock_openai.return_value = mock_client
        
        result = classifier.classify(
            sender="spammer@example.com",
            subject="You won!",
            snippet="Claim your prize",
            body_text="Click here",
            gmail_id="test_spam",
        )
        
        assert result is not None
        assert result.rationale == "LLM classification"

    @patch("openai.OpenAI")
    def test_malformed_json_returns_none(self, mock_openai):
        """Test that malformed JSON response returns None."""
        classifier = self._make_classifier()
        
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_openai_response("not json at all")
        mock_openai.return_value = mock_client
        
        result = classifier.classify(
            sender="test@example.com",
            subject="Test",
            snippet="",
            body_text="",
            gmail_id="test_malformed",
        )
        
        assert result is None

    @patch("openai.OpenAI")
    def test_missing_confidence_returns_zero(self, mock_openai):
        """Test that missing confidence field defaults to 0.0."""
        classifier = self._make_classifier()
        
        no_conf_json = json.dumps({
            "label": "OTHER",
            # no confidence field
            "rationale": "No confidence given.",
            "action_hint": None,
            "needs_review": False,
        })
        
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_openai_response(no_conf_json)
        mock_openai.return_value = mock_client
        
        result = classifier.classify(
            sender="test@example.com",
            subject="Test",
            snippet="",
            body_text="",
            gmail_id="test_no_conf",
        )
        
        assert result is not None
        assert result.confidence == 0.0
        assert result.label == "OTHER"

    @patch("openai.OpenAI")
    def test_body_truncated_to_max_chars(self, mock_openai):
        """Test that body_text is truncated to max_body_chars."""
        classifier = LLMClassifier(api_key="sk-test-key", max_body_chars=50)
        
        valid_json = json.dumps({
            "label": "NOTIFICATION",
            "confidence": 0.50,
            "rationale": "Test.",
            "action_hint": None,
            "needs_review": False,
        })
        
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_openai_response(valid_json)
        mock_openai.return_value = mock_client
        
        long_body = "A" * 200
        result = classifier.classify(
            sender="test@example.com",
            subject="Test",
            snippet="",
            body_text=long_body,
            gmail_id="test_truncate",
        )
        
        assert result is not None
        # Verify the body was truncated in the API call
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        user_msg = call_kwargs["messages"][1]["content"]
        # Body should be truncated to 50 chars
        assert "A" * 50 in user_msg
        assert "A" * 51 not in user_msg
