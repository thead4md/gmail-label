"""Tests for P1D: the watch loop hot-reloads the ML model on mtime change.

A retrain rewrites model.joblib with a fresh mtime. The cached loader
detects the new mtime and rebinds the in-memory classifier — no process
restart required. Unchanged file means the cached classifier is reused.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import mailmind.main as main_mod
from mailmind.ml.model import MLClassifier
from mailmind.ml.train import train_model_from_data


@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    """Isolate MLClassifier's storage to a temp dir for each test."""
    monkeypatch.setattr("mailmind.ml.model.DEFAULT_MODEL_DIR", tmp_path)
    # Reset module-level cache so prior tests can't leak state.
    main_mod._MODEL_CACHE.update({"path": None, "mtime": None, "classifier": None})
    yield tmp_path


def _train_tiny_model(model_dir: Path) -> MLClassifier:
    """Train and save a minimal model in the temp model_dir."""
    corpus = [
        "meeting project update tomorrow morning team",
        "invoice payment due now",
        "unsubscribe from newsletter please",
        "friendly project check in tomorrow",
        "invoice past due payment now",
        "unsubscribe link footer newsletter",
    ]
    labels = ["CALENDAR", "FINANCE", "NEWSLETTER",
              "CALENDAR", "FINANCE", "NEWSLETTER"]
    return train_model_from_data(corpus, labels, model_dir=model_dir)


class TestModelHotReload:
    def test_no_model_returns_none(self, model_dir):
        assert main_mod._load_ml_classifier_cached() is None

    def test_cached_when_unchanged(self, model_dir):
        _train_tiny_model(model_dir)
        first = main_mod._load_ml_classifier_cached()
        second = main_mod._load_ml_classifier_cached()
        assert first is not None
        # Same object identity = cache hit (no disk re-read).
        assert first is second

    def test_reloads_on_mtime_change(self, model_dir):
        _train_tiny_model(model_dir)
        first = main_mod._load_ml_classifier_cached()
        assert first is not None

        # Simulate a retrain: bump the file's mtime forward.
        model_path = first.get_model_path()
        new_mtime = model_path.stat().st_mtime + 60
        os.utime(model_path, (new_mtime, new_mtime))

        second = main_mod._load_ml_classifier_cached()
        assert second is not None
        # Different object identity = cache invalidated and re-loaded.
        assert second is not first

    def test_handles_disappearing_model(self, model_dir):
        _train_tiny_model(model_dir)
        loaded = main_mod._load_ml_classifier_cached()
        assert loaded is not None

        # Delete the model file mid-flight.
        loaded.get_model_path().unlink()

        result = main_mod._load_ml_classifier_cached()
        assert result is None
        assert main_mod._MODEL_CACHE["classifier"] is None
