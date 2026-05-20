"""Shared pytest fixtures for MailMind tests.

Provides mock fixtures for external dependencies like the DeepSeek LLM client.
All LLM API calls must be mocked in tests (no real API calls).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_deepseek_client():
    """Returns a MagicMock DeepSeekClient that returns a default LLMResult.

    Simulates a successful LLM classification with high confidence.
    """
    from mailmind.llm.deepseek import LLMResult

    client = MagicMock()
    client.classify_email.return_value = LLMResult(
        primary_label="PERSONAL",
        llm_confidence=0.92,
        reasoning="Directly addressed personal email.",
        model_available=True,
    )
    return client


@pytest.fixture
def mock_deepseek_disabled():
    """Returns a MagicMock that simulates LLM disabled (model_available=False).

    Simulates a failed or unavailable LLM classification.
    """
    from mailmind.llm.deepseek import LLMResult

    client = MagicMock()
    client.classify_email.return_value = LLMResult(
        primary_label="NOTIFICATION",
        llm_confidence=0.0,
        reasoning="",
        model_available=False,
    )
    return client
