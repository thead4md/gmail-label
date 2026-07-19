"""Tests for path-resolution helpers in ingestion/auth.py.

Pins the fix for a bug found in the wild: MAILMIND_DATA_DIR=~/.mailmind
set in a shell rc with a quoted tilde caused Path("~/.mailmind") to be
interpreted as a literal directory named "~" in the cwd, breaking
authenticate() with a confusing FileNotFoundError.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mailmind.ingestion.auth import SCOPES, _app_dir, _credentials_path


class TestScopes:
    """Pins the Phase 3A scope addition (send, not compose)."""

    def test_gmail_send_scope_present(self):
        assert "https://www.googleapis.com/auth/gmail.send" in SCOPES

    def test_gmail_compose_scope_absent(self):
        # Deliberate scope-minimization decision: drafts live only in
        # MailMind's own `drafts` table, never Gmail's native Drafts folder.
        assert "https://www.googleapis.com/auth/gmail.compose" not in SCOPES


class TestAppDirExpansion:
    def test_tilde_in_env_var_is_expanded(self, monkeypatch, tmp_path):
        """MAILMIND_DATA_DIR=~/foo must resolve to $HOME/foo, not literal '~/foo'."""
        # Pin HOME so the expansion is deterministic and isolated.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("MAILMIND_DATA_DIR", "~/.mailmind")
        resolved = _app_dir()
        assert "~" not in str(resolved)
        assert resolved == tmp_path / ".mailmind"

    def test_absolute_path_in_env_var_passes_through(self, monkeypatch, tmp_path):
        custom = tmp_path / "custom-data"
        monkeypatch.setenv("MAILMIND_DATA_DIR", str(custom))
        assert _app_dir() == custom

    def test_no_env_var_falls_back_to_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("MAILMIND_DATA_DIR", raising=False)
        assert _app_dir() == tmp_path / ".mailmind"

    def test_credentials_path_is_under_app_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MAILMIND_DATA_DIR", "~/some/data")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert _credentials_path() == tmp_path / "some" / "data" / "credentials.json"
