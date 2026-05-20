"""Configuration management for MailMind Pass 7+.

Provides a single source of truth for runtime configuration,
including DeepSeek LLM settings loaded from environment variables.

All sensitive values (API keys) are read from environment only
and are never stored in config files.
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Optional

LOG = logging.getLogger(__name__)


@dataclass
class MailMindConfig:
    """Runtime configuration loaded from environment variables.

    Attributes:
        deepseek_api_key: DeepSeek API key. If empty/absent, LLM is disabled.
        deepseek_model: DeepSeek model name (default: "deepseek-chat").
        deepseek_base_url: DeepSeek API base URL (default: "https://api.deepseek.com/v1").
        llm_skip_threshold: Skip LLM if rules score >= this value (default: 70).
        llm_confidence_override: Override label if LLM confidence >= this (default: 0.90).
        llm_max_calls_per_run: Maximum LLM API calls per pipeline run (default: 10).
        llm_enabled: True only if deepseek_api_key is non-empty.
    """
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    llm_skip_threshold: int = 70
    llm_confidence_override: float = 0.90
    llm_max_calls_per_run: int = 10
    llm_enabled: bool = False

    @classmethod
    def from_env(cls) -> "MailMindConfig":
        """Load configuration from environment variables.

        Reads:
            DEEPSEEK_API_KEY           — Required for LLM; absent/empty → LLM disabled
            DEEPSEEK_MAX_CALLS_PER_RUN — Override max calls per run (default 10)
            DEEPSEEK_MODEL             — Override model name (default deepseek-chat)
            DEEPSEEK_BASE_URL          — Override base URL (default https://api.deepseek.com/v1)

        Returns:
            MailMindConfig with values populated from environment.
        """
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        llm_enabled = bool(api_key)

        # Parse max calls, default to 10
        try:
            max_calls = int(os.environ.get("DEEPSEEK_MAX_CALLS_PER_RUN", "10"))
        except (ValueError, TypeError):
            max_calls = 10
            LOG.warning("Invalid DEEPSEEK_MAX_CALLS_PER_RUN, using default 10")

        config = cls(
            deepseek_api_key=api_key,
            deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip(),
            deepseek_base_url=os.environ.get(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
            ).strip(),
            llm_skip_threshold=70,
            llm_confidence_override=0.90,
            llm_max_calls_per_run=max_calls,
            llm_enabled=llm_enabled,
        )

        LOG.debug(
            "MailMindConfig loaded: llm_enabled=%s, model=%s, max_calls=%d",
            config.llm_enabled,
            config.deepseek_model,
            config.llm_max_calls_per_run,
        )
        return config
