"""Tests for ML model directory resolution.

On Fly, MAILMIND_DATA_DIR=/data/.mailmind points at the persistent volume.
Without honoring that env var, the model lands on ephemeral container
storage and is lost on every machine restart — wasting an extra retrain
cycle and burning LLM calls until the model is rebuilt.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _reload_model_module():
    """Re-import mailmind.ml.model so DEFAULT_MODEL_DIR re-evaluates."""
    import mailmind.ml.model as mod
    importlib.reload(mod)
    return mod


class TestModelDirResolution:
    def test_honours_mailmind_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MAILMIND_DATA_DIR", str(tmp_path))
        mod = _reload_model_module()
        assert mod._default_model_dir() == tmp_path / "models"

    def test_expands_tilde_in_data_dir(self, monkeypatch, tmp_path):
        """MAILMIND_DATA_DIR=~/foo (quoted tilde in a shell rc) must expand."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("MAILMIND_DATA_DIR", "~/.mailmind")
        mod = _reload_model_module()
        resolved = mod._default_model_dir()
        assert "~" not in str(resolved)
        assert resolved == tmp_path / ".mailmind" / "models"

    def test_falls_back_to_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MAILMIND_DATA_DIR", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        mod = _reload_model_module()
        assert mod._default_model_dir() == tmp_path / ".mailmind" / "models"
