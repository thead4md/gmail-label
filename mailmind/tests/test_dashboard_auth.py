"""Tests for dashboard authentication helpers."""
import os
import pytest

from mailmind.dashboard.app import _make_auth_token, _valid_auth_token, _auth_secret


def test_auth_token_validates_with_same_secret():
    """A token made with secret 's1' validates with 's1'."""
    token = _make_auth_token("s1")
    assert _valid_auth_token(token, "s1")


def test_auth_token_fails_with_different_secret():
    """A token made with secret 's1' fails validation with secret 's2'."""
    token = _make_auth_token("s1")
    assert not _valid_auth_token(token, "s2")


def test_auth_secret_returns_dashboard_secret_when_set(monkeypatch):
    """_auth_secret returns DASHBOARD_SECRET when the env var is set."""
    monkeypatch.setenv("DASHBOARD_SECRET", "my-secret-key")
    result = _auth_secret("fallback-password")
    assert result == "my-secret-key"


def test_auth_secret_returns_password_when_env_unset(monkeypatch):
    """_auth_secret returns the passed password when DASHBOARD_SECRET is unset."""
    monkeypatch.delenv("DASHBOARD_SECRET", raising=False)
    result = _auth_secret("my-password")
    assert result == "my-password"


def test_auth_secret_returns_password_when_env_blank(monkeypatch):
    """_auth_secret returns the passed password when DASHBOARD_SECRET is blank."""
    monkeypatch.setenv("DASHBOARD_SECRET", "")
    result = _auth_secret("my-password")
    assert result == "my-password"


def test_auth_secret_returns_password_when_env_whitespace(monkeypatch):
    """_auth_secret returns the passed password when DASHBOARD_SECRET is only whitespace."""
    monkeypatch.setenv("DASHBOARD_SECRET", "   ")
    result = _auth_secret("my-password")
    assert result == "my-password"
