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
from pathlib import Path

DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

LOG = logging.getLogger(__name__)

_DOTENV_LOADED = False


def load_env_file() -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (zero-dependency).

    Search order (first existing wins): $MAILMIND_ENV_FILE, ./.env, the package's
    own mailmind/.env, then ~/.mailmind/.env. Existing environment variables are
    NEVER overwritten — real env (e.g. Fly secrets) always takes precedence over
    the file. Idempotent: only runs once per process. Lines starting with '#' and
    blank lines are ignored; surrounding quotes on values are stripped.
    """
    global _DOTENV_LOADED
    # Never load a real .env during tests — it would clobber monkeypatched env
    # and break isolation. Opt-out also available via MAILMIND_SKIP_DOTENV.
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("MAILMIND_SKIP_DOTENV"):
        return
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    candidates = [
        os.environ.get("MAILMIND_ENV_FILE", "").strip(),
        str(Path.cwd() / ".env"),
        str(Path(__file__).resolve().parent / ".env"),   # mailmind/.env
        os.path.expanduser("~/.mailmind/.env"),
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.lower().startswith("export "):
                        line = line[len("export "):].lstrip()
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
            LOG.info("Loaded environment from %s", path)
        except Exception as exc:
            LOG.warning("Failed to read env file %s: %s", path, exc)
        return  # only the first existing file is loaded


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

    # Content/sender blend weights
    blend_enabled: bool = True
    content_weight: float = 0.80
    sender_weight: float = 0.20
    sender_prior_min_count: int = 3

    # Data directory
    data_dir: str = field(
        default_factory=lambda: os.path.expanduser(
            os.environ.get("MAILMIND_DATA_DIR", "~/.mailmind")
        )
    )

    # Mailbox accounts. The first entry is the primary account that existing
    # single-account data is attributed to. Configured via MAILMIND_ACCOUNTS
    # (comma-separated emails); falls back to [MAILMIND_USER_EMAIL].
    accounts: list = field(default_factory=list)

    @property
    def primary_account(self) -> str:
        """The primary mailbox account (first configured), or '' if none."""
        return self.accounts[0] if self.accounts else ""

    @staticmethod
    def load_accounts() -> list:
        """Resolve the configured mailbox accounts from the environment.

        MAILMIND_ACCOUNTS is a comma-separated list of email addresses. If
        unset, falls back to a single-element list of MAILMIND_USER_EMAIL.
        Order matters: the first account is the primary one.
        """
        raw = os.environ.get("MAILMIND_ACCOUNTS", "").strip()
        if not raw:
            raw = os.environ.get("MAILMIND_USER_EMAIL", "").strip()
        return [a.strip() for a in raw.split(",") if a.strip()]

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
        load_env_file()  # pick up a local .env (no-op on Fly where secrets are set)
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

        blend_enabled = os.environ.get("BLEND_ENABLED", "true").lower() != "false"
        try:
            content_weight = float(os.environ.get("CONTENT_WEIGHT", "0.80"))
            sender_weight = float(os.environ.get("SENDER_WEIGHT", "0.20"))
            sender_prior_min_count = int(os.environ.get("SENDER_PRIOR_MIN_COUNT", "3"))
        except (ValueError, TypeError):
            content_weight, sender_weight, sender_prior_min_count = 0.80, 0.20, 3

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
            accounts=cls.load_accounts(),
            blend_enabled=blend_enabled,
            content_weight=content_weight,
            sender_weight=sender_weight,
            sender_prior_min_count=sender_prior_min_count,
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
