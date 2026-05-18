"""Gmail OAuth2 authentication helpers for MailMind.

Provides authenticate() and build_gmail_service() helpers.

Token storage prefers `keyring`. If unavailable, falls back to an encrypted
local token file using `cryptography.fernet`.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

try:
    import keyring  # type: ignore
except Exception:
    keyring = None

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:
    Fernet = None  # type: ignore

LOG = logging.getLogger(__name__)

# Minimal scopes required for MVP
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
]


def _app_dir() -> Path:
    p = Path(os.environ.get("MAILMIND_APP_DIR", "~/.mailmind")).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _credentials_path() -> Path:
    return _app_dir() / "credentials.json"


def _token_key_name() -> str:
    return "mailmind_gmail_token"


def _token_file() -> Path:
    return _app_dir() / "tokens.json.enc"


def _fernet_key_file() -> Path:
    return _app_dir() / "fernet.key"


def _get_fernet() -> Optional["Fernet"]:
    if Fernet is None:
        return None
    key_file = _fernet_key_file()
    if key_file.exists():
        key = key_file.read_bytes()
    else:
        key = Fernet.generate_key()
        key_file.write_bytes(key)
        try:
            key_file.chmod(0o600)
        except Exception:
            pass
    return Fernet(key)


def _save_token_to_keyring(token_json: str) -> bool:
    if keyring is None:
        return False
    try:
        keyring.set_password("mailmind", _token_key_name(), token_json)
        return True
    except Exception:
        LOG.debug("keyring set_password failed", exc_info=True)
        return False


def _load_token_from_keyring() -> Optional[str]:
    if keyring is None:
        return None
    try:
        val = keyring.get_password("mailmind", _token_key_name())
        return val
    except Exception:
        LOG.debug("keyring get_password failed", exc_info=True)
        return None


def _save_token_local_encrypted(token_json: str) -> bool:
    f = _get_fernet()
    if f is None:
        return False
    enc = f.encrypt(token_json.encode("utf-8"))
    _token_file().write_bytes(enc)
    try:
        _token_file().chmod(0o600)
    except Exception:
        pass
    return True


def _load_token_local_encrypted() -> Optional[str]:
    f = _get_fernet()
    if f is None:
        return None
    tf = _token_file()
    if not tf.exists():
        return None
    try:
        enc = tf.read_bytes()
        dec = f.decrypt(enc)
        return dec.decode("utf-8")
    except Exception:
        LOG.debug("Failed to decrypt local token", exc_info=True)
        return None


def _load_stored_token() -> Optional[str]:
    # Try keyring first
    val = _load_token_from_keyring()
    if val:
        return val
    # Fallback to local encrypted
    return _load_token_local_encrypted()


def _save_stored_token(token_json: str) -> None:
    if not _save_token_to_keyring(token_json):
        if not _save_token_local_encrypted(token_json):
            LOG.warning("Failed to persist token to keyring or local encrypted file")


def authenticate(scopes: Optional[list[str]] = None) -> Credentials:
    """Authenticate the user and return valid google Credentials.

    - Loads client secrets from ~/.mailmind/credentials.json
    - Attempts to load stored tokens from keyring or encrypted local file
    - Refreshes tokens if expired
    - Runs interactive InstalledAppFlow if no valid tokens exist
    """
    scopes = scopes or SCOPES
    creds: Optional[Credentials] = None

    stored = _load_stored_token()
    if stored:
        try:
            info = json.loads(stored)
            creds = Credentials.from_authorized_user_info(info, scopes=scopes)
            LOG.debug("Loaded credentials from storage (not logging token)")
        except Exception:
            LOG.debug("Failed to load credentials from storage", exc_info=True)
            creds = None

    # If creds are present but expired, refresh
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_stored_token(creds.to_json())
            LOG.info("Refreshed expired credentials")
            return creds
        except Exception:
            LOG.debug("Failed to refresh credentials, discarding", exc_info=True)
            creds = None

    # If no valid creds, run InstalledAppFlow
    if not creds or not creds.valid:
        cred_path = _credentials_path()
        if not cred_path.exists():
            raise FileNotFoundError(f"OAuth client credentials not found at {cred_path}")
        flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), scopes=scopes)
        creds = flow.run_local_server(port=0)
        # Persist token
        try:
            _save_stored_token(creds.to_json())
        except Exception:
            LOG.debug("Failed to persist newly obtained token", exc_info=True)

    return creds


def build_gmail_service(creds: Credentials):
    """Return a googleapiclient discovery service for Gmail.

    Caller should pass valid credentials (authenticate()).
    """
    from googleapiclient.discovery import build

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return service

