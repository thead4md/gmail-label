"""Tests for P2D-1: watch-loop heartbeat.

The watch loop writes last_heartbeat_ts to system_state every cycle. The
dashboard reads it and shows "Watcher silent for X min" when stale, so a
silently hung watcher is visible instead of mysterious. Heartbeat write
errors must never propagate (a heartbeat that crashes the loop would
defeat the whole point).
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

import mailmind.main as main_mod
from mailmind.dashboard.helpers import get_heartbeat_status
from mailmind.storage.database import Database


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


class TestHeartbeatWrite:
    def test_records_current_timestamp(self, db: Database):
        before = int(time.time())
        main_mod._record_heartbeat(db)
        stamped = int(db.get_state(main_mod.HEARTBEAT_KEY))
        assert before <= stamped <= int(time.time())

    def test_subsequent_writes_overwrite(self, db: Database):
        main_mod._record_heartbeat(db)
        first = int(db.get_state(main_mod.HEARTBEAT_KEY))
        time.sleep(1.1)
        main_mod._record_heartbeat(db)
        second = int(db.get_state(main_mod.HEARTBEAT_KEY))
        assert second > first

    def test_write_failure_does_not_propagate(self):
        """The watch loop must keep running even if heartbeat writes fail."""
        broken_db = MagicMock()
        broken_db.set_state.side_effect = RuntimeError("disk full")
        # Must not raise.
        main_mod._record_heartbeat(broken_db)


class TestHeartbeatStatusHelper:
    def test_never_when_no_heartbeat(self):
        status = get_heartbeat_status(None)
        assert status["status"] == "never"
        assert status["seconds_ago"] is None

    def test_fresh_within_threshold(self):
        recent = int(time.time()) - 30  # 30s ago, well within threshold
        status = get_heartbeat_status(recent, expected_interval_seconds=120,
                                       stale_after_intervals=3)
        assert status["status"] == "fresh"
        assert status["seconds_ago"] is not None
        assert status["seconds_ago"] >= 30

    def test_stale_past_threshold(self):
        # 10 minutes ago with default thresholds (120s * 3 = 360s = 6min): stale.
        old = int(time.time()) - 600
        status = get_heartbeat_status(old)
        assert status["status"] == "stale"
        assert "silent" in status["human"]

    def test_threshold_boundary_is_inclusive_of_fresh(self):
        """Exactly at the threshold should still be fresh; one second past = stale."""
        threshold = 360  # default 120 * 3
        at_boundary = int(time.time()) - threshold
        just_over = int(time.time()) - (threshold + 5)
        assert get_heartbeat_status(at_boundary)["status"] == "fresh"
        assert get_heartbeat_status(just_over)["status"] == "stale"

    def test_custom_threshold_respected(self):
        """A user with a long poll interval gets a proportionally long threshold."""
        # 30 min poll, 2 missed cycles allowed = stale after 1h.
        thirty_min_ago = int(time.time()) - 30 * 60
        # Still fresh under 30-min poll (1 missed cycle).
        status = get_heartbeat_status(thirty_min_ago,
                                       expected_interval_seconds=1800,
                                       stale_after_intervals=2)
        assert status["status"] == "fresh"
