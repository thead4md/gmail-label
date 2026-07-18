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
#
# NOTE (Phase 3A): "gmail.send" was added here to support the compose/reply
# feature. Deliberately NOT adding "gmail.compose" — drafts live only in
# MailMind's own `drafts` table, never in Gmail's native Drafts folder, which
# keeps this scope list (and the re-consent surface) as small as possible.
#
# IMPORTANT — broadening SCOPES is a breaking change for already-stored
# tokens: a token minted under the old 3-scope list will fail to refresh
# once it's checked against this new list (there is no scope-diffing logic
# in this file — see load_stored_credentials()/authenticate() below), so
# every already-connected mailbox needs a one-time interactive re-consent
# run locally (this can't run headless, e.g. on Fly.io) before the broadened
# scope actually works:
#     python -m mailmind.main auth --account <email>
# Run this once per already-connected mailbox (both current accounts) after
# this change lands.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


def _app_dir() -> Path:
    custom = os.environ.get("MAILMIND_DATA_DIR")
    if custom:
        # Path() does NOT expand "~" — without expanduser, "~/.mailmind" turns
        # into a literal directory called "~" in the cwd. Bit me in the wild
        # when the env var was set with a quoted tilde in a shell rc.
        path = Path(custom).expanduser()
    else:
        path = Path.home() / ".mailmind"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _credentials_path() -> Path:
    return _app_dir() / "credentials.json"


def _account_slug(account: Optional[str]) -> str:
    """Filesystem/keyring-safe slug for an account email (empty for primary)."""
    if not account:
        return ""
    return "".join(c if c.isalnum() else "_" for c in account)


def _token_key_name(account: Optional[str] = None) -> str:
    base = "mailmind_gmail_token"
    return base if not account else f"{base}__{_account_slug(account)}"


def _token_file(account: Optional[str] = None) -> Path:
    name = "tokens.json.enc" if not account else f"tokens.{_account_slug(account)}.json.enc"
    return _app_dir() / name


def _token_env_var(account: Optional[str] = None) -> str:
    """Env var holding a token JSON, as a headless/Fly-secret fallback.

    Primary account (account=None) -> GMAIL_TOKEN.
    Named account -> GMAIL_TOKEN_<UPPER_SLUG>, e.g. GMAIL_TOKEN_DUDAS_ADAM_MCSSZ_HU.
    """
    if not account:
        return "GMAIL_TOKEN"
    return f"GMAIL_TOKEN_{_account_slug(account).upper()}"


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


def _plain_token_file(account: Optional[str] = None) -> Path:
    name = "token.json" if not account else f"token.{_account_slug(account)}.json"
    return _app_dir() / name


def _save_token_to_keyring(token_json: str, account: Optional[str] = None) -> bool:
    if keyring is None:
        return False
    try:
        keyring.set_password("mailmind", _token_key_name(account), token_json)
        return True
    except Exception:
        LOG.debug("keyring set_password failed", exc_info=True)
        return False


def _load_token_from_keyring(account: Optional[str] = None) -> Optional[str]:
    if keyring is None:
        return None
    try:
        val = keyring.get_password("mailmind", _token_key_name(account))
        return val
    except Exception:
        LOG.debug("keyring get_password failed", exc_info=True)
        return None


def _save_token_local_encrypted(token_json: str, account: Optional[str] = None) -> bool:
    f = _get_fernet()
    if f is None:
        return False
    enc = f.encrypt(token_json.encode("utf-8"))
    _token_file(account).write_bytes(enc)
    try:
        _token_file(account).chmod(0o600)
    except Exception:
        pass
    return True


def _load_token_local_encrypted(account: Optional[str] = None) -> Optional[str]:
    f = _get_fernet()
    if f is None:
        return None
    tf = _token_file(account)
    if not tf.exists():
        return None
    try:
        enc = tf.read_bytes()
        dec = f.decrypt(enc)
        return dec.decode("utf-8")
    except Exception:
        LOG.debug("Failed to decrypt local token", exc_info=True)
        return None


def _load_stored_token(account: Optional[str] = None) -> Optional[str]:
    # Try keyring first
    val = _load_token_from_keyring(account)
    if val:
        return val
    # Fallback to local encrypted
    val = _load_token_local_encrypted(account)
    if val:
        return val
    # Plain JSON fallback for headless server environments (e.g. Fly.io)
    plain_path = _plain_token_file(account)
    if os.environ.get("MAILMIND_DATA_DIR") and plain_path.exists():
        return plain_path.read_text()
    # Env-var fallback (Fly secret): GMAIL_TOKEN / GMAIL_TOKEN_<SLUG>
    env_val = os.environ.get(_token_env_var(account), "").strip()
    if env_val:
        return env_val
    return None


def _save_stored_token(token_json: str, account: Optional[str] = None) -> None:
    if not _save_token_to_keyring(token_json, account):
        if not _save_token_local_encrypted(token_json, account):
            # Plain JSON fallback for headless server environments (e.g. Fly.io)
            if os.environ.get("MAILMIND_DATA_DIR"):
                try:
                    plain_path = _plain_token_file(account)
                    plain_path.write_text(token_json)
                    plain_path.chmod(0o600)
                except Exception:
                    LOG.warning("Failed to persist token to keyring, encrypted file, or plain JSON")
            else:
                LOG.warning("Failed to persist token to keyring or local encrypted file")


def load_stored_credentials(
    account: Optional[str] = None, scopes: Optional[list[str]] = None
) -> Optional[Credentials]:
    """Load (and refresh) stored credentials WITHOUT any interactive flow.

    Returns valid Credentials for *account*, or None if no usable stored token
    exists. Used by the headless watch loop so a not-yet-connected mailbox is
    skipped rather than blocking on an OAuth prompt.
    """
    scopes = scopes or SCOPES
    stored = _load_stored_token(account)
    if not stored:
        return None
    try:
        info = json.loads(stored)
        creds = Credentials.from_authorized_user_info(info, scopes=scopes)
    except Exception:
        LOG.debug("Failed to load credentials from storage for %s", account, exc_info=True)
        return None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_stored_token(creds.to_json(), account)
            LOG.info("Refreshed expired credentials for %s", account or "primary")
        except Exception as exc:
            # WARNING (not debug): a refresh failure here (e.g. invalid_grant
            # from a revoked/expired token) is indistinguishable from "never
            # connected" to the caller, which then falls back to interactive
            # auth. On a headless deploy that fallback can never complete, so
            # this needs to be visible under default INFO logging, not hidden
            # at DEBUG.
            LOG.warning(
                "Failed to refresh credentials for %s: %s",
                account or "primary", exc, exc_info=True,
            )
            return None
    return creds if (creds and creds.valid) else None


def authenticate(
    scopes: Optional[list[str]] = None, account: Optional[str] = None
) -> Credentials:
    """Authenticate the user and return valid google Credentials.

    - Loads client secrets from ~/.mailmind/credentials.json
    - Attempts to load stored tokens from keyring or encrypted local file
    - Refreshes tokens if expired
    - Runs interactive InstalledAppFlow if no valid tokens exist

    The optional *account* selects per-mailbox token storage (multi-account).
    """
    scopes = scopes or SCOPES

    creds = load_stored_credentials(account, scopes)
    if creds:
        return creds

    # If no valid creds, run InstalledAppFlow
    cred_path = _credentials_path()
    if not cred_path.exists():
        raise FileNotFoundError(f"OAuth client credentials not found at {cred_path}")
    flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), scopes=scopes)
    creds = flow.run_local_server(port=0)
    # Persist token
    try:
        _save_stored_token(creds.to_json(), account)
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

