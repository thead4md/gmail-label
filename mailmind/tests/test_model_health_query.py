"""Tests for get_ml_model_metadata — the dashboard's Model Health panel reader.

Found in production: the dashboard was reading from a non-existent
ml_model_metadata table while training writes to system_state with key
'ml_model:<name>'. The try/except silently returned None so the dashboard
always showed "No model trained yet" even with a fresh model on disk.
Pinning the fix so this can't regress.
"""
from __future__ import annotations

import json
import time

import pytest

from mailmind.storage.database import Database
from mailmind.storage.queries import get_ml_model_metadata


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def _stamp_model_metadata(db: Database, *,
                          model_name: str = "pass4_baseline.joblib",
                          num_samples: int = 631,
                          accuracy: float = 0.946,
                          class_names=None,
                          when: int | None = None) -> int:
    """Mirror what ml.train._save_model_metadata_to_db does."""
    when = when or int(time.time())
    payload = {
        "version": "1.0.0",
        "pipeline_used": "ml",
        "num_samples": num_samples,
        "accuracy": accuracy,
        "class_names": class_names or ["WORK", "NEWSLETTER"],
        "trained_at": "2026-06-01T19:32:04Z",
    }
    db.execute_sql(
        "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES (?, ?, ?)",
        (f"ml_model:{model_name}", json.dumps(payload), when),
    )
    db._conn.commit()
    return when


class TestGetMlModelMetadata:
    def test_returns_none_when_no_metadata(self, db: Database):
        assert get_ml_model_metadata(db) is None

    def test_reads_from_system_state(self, db: Database):
        """Was the bug: reader looked at the wrong table; ensure system_state works."""
        when = _stamp_model_metadata(db, num_samples=631, accuracy=0.946)
        meta = get_ml_model_metadata(db)
        assert meta is not None
        # Dashboard-shaped fields, not the raw ModelMetadata shape.
        assert meta["created_at"] == when           # for format_unix_ts
        assert meta["training_samples"] == 631      # NOT num_samples
        assert meta["accuracy"] == 0.946
        assert "WORK" in meta["class_names"]

    def test_returns_latest_when_multiple_models(self, db: Database):
        _stamp_model_metadata(db, model_name="old.joblib",
                               num_samples=100, when=1000)
        _stamp_model_metadata(db, model_name="new.joblib",
                               num_samples=999, when=2000)
        meta = get_ml_model_metadata(db)
        assert meta["training_samples"] == 999  # latest by updated_at

    def test_handles_corrupted_json(self, db: Database):
        when = int(time.time())
        db.execute_sql(
            "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?)",
            ("ml_model:broken.joblib", "not-json", when),
        )
        db._conn.commit()
        # Doesn't crash; returns a dict with created_at set, fields None.
        meta = get_ml_model_metadata(db)
        assert meta is not None
        assert meta["created_at"] == when
        assert meta["training_samples"] is None
