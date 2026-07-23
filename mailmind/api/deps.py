"""Shared FastAPI dependencies: DB singleton, per-account executor cache, LLM client.

Mirrors the exact caching shape the Streamlit dashboard used
(@st.cache_resource get_db / get_action_executor) — one Database connection
for the process lifetime, one ActionExecutor per mailbox account, built
lazily and reused.
"""
from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from mailmind.storage.database import Database

_db_lock = threading.Lock()
_db: Optional[Database] = None


def get_db() -> Database:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                db_path = Path(os.environ.get("MAILMIND_DB_PATH", "~/.mailmind/mailmind.db")).expanduser()
                _db = Database(db_path)
    return _db


@lru_cache(maxsize=None)
def _get_action_executor_cached(account: Optional[str]):
    """One ActionExecutor per mailbox account, or None if it has no stored
    credentials. lru_cache keys on `account`, matching the dashboard's
    per-account @st.cache_resource behavior (a bare singleton previously
    resolved the PRIMARY mailbox's token for every account — see
    dashboard/app.py's get_action_executor docstring for the incident this
    guards against)."""
    from mailmind.actions.executor import ActionExecutor
    from mailmind.actions.safety import SafetyPolicy
    from mailmind.ingestion.auth import build_gmail_service, load_stored_credentials

    creds = load_stored_credentials(account)
    if creds is None:
        return None
    service = build_gmail_service(creds)
    dry_run = os.environ.get("MAILMIND_DRY_RUN", "0") == "1"
    db = get_db()
    return ActionExecutor(service=service, db=db, safety_policy=SafetyPolicy(dry_run=dry_run, db=db))


def get_action_executor(account: Optional[str] = None):
    return _get_action_executor_cached(account)


@lru_cache(maxsize=None)
def _get_calendar_client_cached(account: Optional[str]):
    """One CalendarClient per mailbox account, or None if it has no stored
    credentials. Mirrors _get_action_executor_cached exactly (per-account
    cache keying for the same reason)."""
    from mailmind.actions.calendar import CalendarClient
    from mailmind.actions.safety import SafetyPolicy
    from mailmind.ingestion.auth import build_calendar_service, load_stored_credentials

    creds = load_stored_credentials(account)
    if creds is None:
        return None
    service = build_calendar_service(creds)
    dry_run = os.environ.get("MAILMIND_DRY_RUN", "0") == "1"
    return CalendarClient(service, SafetyPolicy(dry_run=dry_run, db=get_db()))


def get_calendar_client(account: Optional[str] = None):
    return _get_calendar_client_cached(account)


@lru_cache(maxsize=1)
def get_llm_client() -> Optional[Any]:
    """DeepSeek client for AI-drafted replies, or None if unconfigured."""
    from mailmind.config import MailMindConfig
    from mailmind.llm.deepseek import DeepSeekClient

    config = MailMindConfig.from_env()
    if not (config.llm_enabled and config.deepseek_api_key):
        return None
    try:
        return DeepSeekClient(config)
    except Exception:
        return None


def get_accounts() -> list[str]:
    from mailmind.config import MailMindConfig
    return MailMindConfig.load_accounts()
