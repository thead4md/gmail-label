"""Tests for MailMind configuration management.

Covers:
- Loading config from environment variables
- LLM disabled when DEEPSEEK_API_KEY is absent or empty
- LLM enabled when DEEPSEEK_API_KEY is set
- Custom overrides for model, base_url, max_calls
"""
from __future__ import annotations

import os

import pytest


class TestMailMindConfig:
    """Tests for MailMindConfig.from_env()."""

    def test_llm_disabled_when_no_key(self):
        """Verify llm_enabled=False when DEEPSEEK_API_KEY is absent."""
        # Ensure the env var is removed
        if "DEEPSEEK_API_KEY" in os.environ:
            del os.environ["DEEPSEEK_API_KEY"]

        from mailmind.config import MailMindConfig

        config = MailMindConfig.from_env()
        assert config.llm_enabled is False
        assert config.deepseek_api_key == ""
        assert config.deepseek_model == "deepseek-chat"
        assert config.deepseek_base_url == "https://api.deepseek.com/v1"
        assert config.llm_max_calls_per_run == 10

    def test_llm_disabled_when_empty_key(self):
        """Verify llm_enabled=False when DEEPSEEK_API_KEY is empty."""
        os.environ["DEEPSEEK_API_KEY"] = ""

        from mailmind.config import MailMindConfig

        config = MailMindConfig.from_env()
        assert config.llm_enabled is False
        assert config.deepseek_api_key == ""

    def test_llm_enabled_with_key(self):
        """Verify llm_enabled=True when DEEPSEEK_API_KEY is set."""
        os.environ["DEEPSEEK_API_KEY"] = "sk-test-key-12345"

        from mailmind.config import MailMindConfig

        config = MailMindConfig.from_env()
        assert config.llm_enabled is True
        assert config.deepseek_api_key == "sk-test-key-12345"
        assert config.deepseek_model == "deepseek-chat"
        assert config.llm_max_calls_per_run == 10

    def test_custom_max_calls(self):
        """Verify DEEPSEEK_MAX_CALLS_PER_RUN overrides default."""
        os.environ["DEEPSEEK_API_KEY"] = "sk-test-key"
        os.environ["DEEPSEEK_MAX_CALLS_PER_RUN"] = "25"

        from mailmind.config import MailMindConfig

        config = MailMindConfig.from_env()
        assert config.llm_max_calls_per_run == 25

    def test_custom_model(self):
        """Verify DEEPSEEK_MODEL overrides default."""
        os.environ["DEEPSEEK_API_KEY"] = "sk-test-key"
        os.environ["DEEPSEEK_MODEL"] = "deepseek-coder"

        from mailmind.config import MailMindConfig

        config = MailMindConfig.from_env()
        assert config.deepseek_model == "deepseek-coder"

    def test_defaults(self):
        """Verify default values of the dataclass."""
        from mailmind.config import MailMindConfig

        config = MailMindConfig()
        assert config.deepseek_api_key == ""
        assert config.deepseek_model == "deepseek-chat"
        assert config.deepseek_base_url == "https://api.deepseek.com/v1"
        assert config.llm_skip_threshold == 70
        assert config.llm_confidence_override == 0.90
        assert config.llm_max_calls_per_run == 10
        assert config.llm_enabled is False
