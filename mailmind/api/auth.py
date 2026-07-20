"""Session auth for the API — ported byte-for-byte from the Streamlit
dashboard's cookie/HMAC/lockout scheme (dashboard/app.py's _check_password
and friends), just moved from a Streamlit cookie-controller component to
plain HTTP Set-Cookie headers.

Same semantics as before:
- DASHBOARD_PASSWORD unset/empty -> no auth required at all.
- A 30-day HMAC-signed cookie (`mm_auth`) so the browser doesn't need to
  resend the password every visit.
- DASHBOARD_SECRET, if set, signs the cookie instead of the plaintext
  password (so a stolen cookie can't be brute-forced against a weak login
  password); falls back to the password when unset.
- Per-browser lockout after 5 failed attempts within a window, keyed by a
  separate long-lived `mm_client_id` cookie — so one anonymous attacker can
  only ever lock out their own counter, never the operator's.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel

from mailmind.api.deps import get_db

AUTH_COOKIE = "mm_auth"
AUTH_DAYS = 30
CLIENT_ID_COOKIE = "mm_client_id"
CLIENT_ID_DAYS = 30
AUTH_MAX_FAILURES = 5
AUTH_LOCKOUT_SECONDS = 300
_AUTH_STATE_KEY = "dashboard_auth_state"


def _cookie_kwargs(max_age_days: int) -> dict:
    # Secure is skipped for plain-http local dev; Fly.io terminates TLS in
    # front of the app, and force_https in fly.toml means real traffic is
    # always HTTPS, so this only ever disables Secure on localhost.
    secure = os.environ.get("MAILMIND_COOKIE_SECURE", "1") != "0"
    return {
        "max_age": max_age_days * 86400,
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "path": "/",
    }


def required_password() -> str:
    return os.environ.get("DASHBOARD_PASSWORD", "").strip()


def _auth_secret(password: str) -> str:
    return os.environ.get("DASHBOARD_SECRET", "").strip() or password


def make_auth_token(secret: str) -> str:
    expiry = int(time.time()) + AUTH_DAYS * 86400
    sig = hmac.new(secret.encode(), str(expiry).encode(), hashlib.sha256).hexdigest()
    return f"{expiry}:{sig}"


def valid_auth_token(token: str, secret: str) -> bool:
    try:
        expiry_str, sig = token.split(":", 1)
        if int(expiry_str) < int(time.time()):
            return False
        expected = hmac.new(secret.encode(), expiry_str.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


def _auth_state_key(client_id: str) -> str:
    return f"{_AUTH_STATE_KEY}:{client_id}"


def lockout_remaining(client_id: str) -> int:
    raw = get_db().get_state(_auth_state_key(client_id))
    if not raw:
        return 0
    try:
        data = json.loads(raw)
    except Exception:
        return 0
    return max(0, int(data.get("locked_until", 0)) - int(time.time()))


def record_auth_failure(client_id: str) -> None:
    key = _auth_state_key(client_id)
    raw = get_db().get_state(key)
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    failures = int(data.get("failures", 0)) + 1
    locked_until = 0
    if failures >= AUTH_MAX_FAILURES:
        locked_until = int(time.time()) + AUTH_LOCKOUT_SECONDS
        failures = 0
    get_db().set_state(key, json.dumps({"failures": failures, "locked_until": locked_until}))


def reset_auth_failures(client_id: str) -> None:
    get_db().set_state(_auth_state_key(client_id), json.dumps({"failures": 0, "locked_until": 0}))


def get_or_set_client_id(response: Response, client_id_cookie: Optional[str]) -> str:
    if client_id_cookie:
        return client_id_cookie
    cid = secrets.token_hex(16)
    response.set_cookie(CLIENT_ID_COOKIE, cid, **_cookie_kwargs(CLIENT_ID_DAYS))
    return cid


def is_authenticated(mm_auth: Optional[str] = Cookie(default=None)) -> bool:
    required = required_password()
    if not required:
        return True
    if not mm_auth:
        return False
    return valid_auth_token(mm_auth, _auth_secret(required))


def require_auth(authed: bool = Depends(is_authenticated)) -> None:
    if not authed:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    password: str


@router.get("/status")
def auth_status(authed: bool = Depends(is_authenticated)) -> dict:
    return {"required": bool(required_password()), "authenticated": authed}


@router.post("/login")
def login(
    body: LoginBody,
    response: Response,
    mm_client_id: Optional[str] = Cookie(default=None),
) -> dict:
    required = required_password()
    if not required:
        return {"authenticated": True}

    client_id = get_or_set_client_id(response, mm_client_id)
    remaining = lockout_remaining(client_id)
    if remaining > 0:
        raise HTTPException(status_code=429, detail=f"Too many attempts. Try again in {remaining}s.")

    if not hmac.compare_digest(body.password, required):
        record_auth_failure(client_id)
        raise HTTPException(status_code=401, detail="Incorrect password.")

    reset_auth_failures(client_id)
    token = make_auth_token(_auth_secret(required))
    response.set_cookie(AUTH_COOKIE, token, **_cookie_kwargs(AUTH_DAYS))
    return {"authenticated": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(AUTH_COOKIE, path="/")
    return {"authenticated": False}
