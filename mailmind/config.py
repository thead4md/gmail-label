"""Configuration management for MailMind Pass 7+.

Provides a single source of truth for runtime configuration,
including DeepSeek LLM and OpenAI-based LLM settings loaded from environment.

All sensitive values (API keys) are read from environment only
and are never stored in config files.
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field

DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

LOG = logging.getLogger(__name__)


@dataclass
class MailMindConfig:
    """Runtime configuration loaded from environment variables.

    DeepSeek LLM (existing):
        deepseek_api_key: DeepSeek API key. If empty/absent, LLM is disabled.
        deepseek_model: DeepSeek model name (default: "deepseek-chat").
        deepseek_base_url: DeepSeek API base URL (default: "https://api.deepseek.com/v1").
        llm_skip_threshold: Skip LLM if rules score >= this value (default: 70).
        llm_confidence_override: Override label if LLM confidence >= this (default: 0.90).
        llm_max_calls_per_run: Max LLM API calls per pipeline run (default: 10).
        llm_enabled: True only if deepseek_api_key is non-empty.

    OpenAI-based external LLM classifier (third-tier fallback):
        openai_llm_enabled: Explicitly enabled via LLM_ENABLED=true.
        openai_api_key: OpenAI API key (from OPENAI_API_KEY).
        openai_model: Model name (default: "gpt-4o-mini").
        openai_rules_threshold: Rules confidence threshold (default: 0.90).
        openai_ml_threshold: ML confidence threshold (default: 0.65).
        openai_max_body_chars: Max chars to include from email body (default: 1500).
    """
    # DeepSeek LLM (existing)
    deepseek_api_key: str = ""
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL
    deepseek_base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    llm_skip_threshold: int = 70
    llm_confidence_override: float = 0.90
    llm_max_calls_per_run: int = 10
    llm_enabled: bool = False

    # OpenAI-based external LLM classifier (third-tier fallback)
    openai_llm_enabled: bool = False
    openai_api_key: str = ""
    openai_model: str = DEFAULT_OPENAI_MODEL
    openai_rules_threshold: float = 0.90
    openai_ml_threshold: float = 0.65
    openai_max_body_chars: int = 1500

    # Data directory
    data_dir: str = field(
        default_factory=lambda: os.path.expanduser(
            os.environ.get("MAILMIND_DATA_DIR", "~/.mailmind")
        )
    )

    @classmethod
    def from_env(cls) -> "MailMindConfig":
        """Load configuration from environment variables.

        Reads:
            DEEPSEEK_API_KEY           - Required for DeepSeek LLM
            DEEPSEEK_MAX_CALLS_PER_RUN - Override max DeepSeek calls per run
            DEEPSEEK_MODEL             - Override DeepSeek model name
            DEEPSEEK_BASE_URL          - Override DeepSeek base URL
            LLM_ENABLED                - Enable OpenAI LLM classifier ("true")
            OPENAI_API_KEY             - OpenAI API key
            LLM_MODEL                  - Override OpenAI model name
            LLM_RULES_THRESHOLD        - Rules confidence threshold
            LLM_ML_THRESHOLD           - ML confidence threshold
            LLM_MAX_BODY_CHARS         - Max body chars to send to LLM

        Returns:
            MailMindConfig with values populated from environment.
        """
        deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        llm_enabled = bool(deepseek_api_key)
        openai_llm_enabled = os.environ.get("LLM_ENABLED", "false").lower() == "true"

        # Parse DeepSeek max calls
        try:
            max_calls = int(os.environ.get("DEEPSEEK_MAX_CALLS_PER_RUN", "10"))
        except (ValueError, TypeError):
            max_calls = 10
            LOG.warning("Invalid DEEPSEEK_MAX_CALLS_PER_RUN, using default 10")

        config = cls(
            deepseek_api_key=deepseek_api_key,
            deepseek_model=os.environ.get("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL).strip(),
            deepseek_base_url=os.environ.get(
                "DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL
            ).strip(),
            llm_skip_threshold=70,
            llm_confidence_override=0.90,
            llm_max_calls_per_run=max_calls,
            llm_enabled=llm_enabled,
            openai_llm_enabled=openai_llm_enabled,
            openai_api_key=openai_api_key,
            openai_model=os.environ.get("LLM_MODEL", DEFAULT_OPENAI_MODEL).strip(),
            openai_rules_threshold=float(
                os.environ.get("LLM_RULES_THRESHOLD", "0.90")
            ),
            openai_ml_threshold=float(
                os.environ.get("LLM_ML_THRESHOLD", "0.65")
            ),
            openai_max_body_chars=int(
                os.environ.get("LLM_MAX_BODY_CHARS", "1500")
            ),
            data_dir=os.path.expanduser(
                os.environ.get("MAILMIND_DATA_DIR", "~/.mailmind")
            ),
        )

        LOG.debug(
            "MailMindConfig loaded: llm_enabled=%s, openai_llm_enabled=%s, "
            "deepseek_model=%s, openai_model=%s, max_calls=%d",
            config.llm_enabled,
            config.openai_llm_enabled,
            config.deepseek_model,
            config.openai_model,
            config.llm_max_calls_per_run,
        )
        return config
