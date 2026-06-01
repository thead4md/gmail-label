"""Tests for P1C: the watch loop self-retrains on cadence or after N corrections.

Two triggers fire _maybe_retrain():
  - CADENCE: >= interval_seconds since the last retrain (default weekly).
  - CORRECTIONS: >= corrections_threshold new user corrections since last train.

Tracked in system_state(last_train_ts, last_train_corrections_count).
Failure must never propagate out of the watch loop.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

import mailmind.main as main_mod
from mailmind.storage.database import Database


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def _log_correction(db: Database, gmail_id: str = "g1"):
    db.execute_sql(
        "INSERT INTO user_corrections "
        "(email_gmail_id, original_label, corrected_label, source) "
        "VALUES (?, ?, ?, ?)",
        (gmail_id, "WORK", "NEWSLETTER", "dashboard"),
    )
    db._conn.commit()


class TestMaybeRetrainTriggers:
    def test_fires_when_never_trained(self, db: Database):
        """No prior last_train_ts -> cadence trigger fires."""
        with patch.object(main_mod, "train_model_from_db") as mock_train:
            mock_train.return_value = MagicMock(metadata=MagicMock(num_samples=10))
            main_mod._maybe_retrain(db)
        mock_train.assert_called_once()
        assert db.get_state("last_train_ts") is not None

    def test_skips_when_within_interval_and_below_corrections(self, db: Database):
        """Recent retrain + few corrections -> no retrain."""
        now = int(time.time())
        db.set_state("last_train_ts", str(now - 60))  # 60s ago
        db.set_state("last_train_corrections_count", "0")

        with patch.object(main_mod, "train_model_from_db") as mock_train:
            main_mod._maybe_retrain(
                db, interval_seconds=7 * 86400, corrections_threshold=5
            )
        mock_train.assert_not_called()

    def test_fires_on_cadence_overdue(self, db: Database):
        """Last train older than interval -> fires regardless of corrections."""
        ten_days_ago = int(time.time()) - 10 * 86400
        db.set_state("last_train_ts", str(ten_days_ago))
        db.set_state("last_train_corrections_count", "0")

        with patch.object(main_mod, "train_model_from_db") as mock_train:
            mock_train.return_value = MagicMock(metadata=MagicMock(num_samples=10))
            main_mod._maybe_retrain(db, interval_seconds=7 * 86400)
        mock_train.assert_called_once()

    def test_fires_on_corrections_threshold(self, db: Database):
        """Within interval, but enough new corrections -> fires."""
        now = int(time.time())
        db.set_state("last_train_ts", str(now - 60))  # recent
        db.set_state("last_train_corrections_count", "0")
        for i in range(5):
            _log_correction(db, gmail_id=f"g{i}")

        with patch.object(main_mod, "train_model_from_db") as mock_train:
            mock_train.return_value = MagicMock(metadata=MagicMock(num_samples=10))
            main_mod._maybe_retrain(
                db, interval_seconds=7 * 86400, corrections_threshold=5
            )
        mock_train.assert_called_once()

    def test_records_baseline_after_train(self, db: Database):
        """After a successful train, both system_state keys are bumped."""
        for i in range(7):
            _log_correction(db, gmail_id=f"g{i}")

        with patch.object(main_mod, "train_model_from_db") as mock_train:
            mock_train.return_value = MagicMock(metadata=MagicMock(num_samples=10))
            main_mod._maybe_retrain(db)

        assert int(db.get_state("last_train_ts") or 0) > 0
        # Corrections baseline = current count, so next run starts from a clean 0 delta.
        assert db.get_state("last_train_corrections_count") == "7"

    def test_skips_when_no_training_data(self, db: Database):
        """train_model_from_db returns None (no data) -> baseline not bumped."""
        with patch.object(main_mod, "train_model_from_db", return_value=None):
            main_mod._maybe_retrain(db)
        # State stays unset so the next cycle will try again.
        assert db.get_state("last_train_ts") is None

    def test_swallows_exceptions(self, db: Database):
        """Any training error is caught so the watch loop continues."""
        with patch.object(main_mod, "train_model_from_db",
                          side_effect=RuntimeError("boom")):
            # Must not raise.
            main_mod._maybe_retrain(db)
