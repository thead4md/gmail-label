"""Tests for intelligence/simulation.py: the "what breaks this week" inbox
simulation (client-strategy reframe §4.6).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mailmind.storage.database import Database
from mailmind.intelligence.simulation import simulate_week, compute_weekly_simulation, ESTIMATE_DAYS

DAY = 86400
NOW = 1_000_000_000  # arbitrary fixed epoch for determinism


@pytest.fixture
def db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    database = Database(db_path)
    yield database
    database.close()
    db_path.unlink(missing_ok=True)


class TestSimulateWeekYouOwe:
    def test_parseable_deadline_within_window_is_included(self):
        items = [{
            "id": 1, "subject": "Budget", "sender": "bob@y.com", "created_at": NOW,
            "reason_json": {"deadlines": ["tomorrow"]},
        }]
        result = simulate_week(items, [], {}, NOW)
        assert len(result) == 1
        assert result[0]["kind"] == "you_owe"
        assert result[0]["is_estimated"] is False
        # "tomorrow" resolves to end-of-day tomorrow, so the gap is somewhere
        # between 1 and 2 days depending on what time of day NOW falls at.
        assert 1.0 <= result[0]["breaks_in_days"] <= 2.0

    def test_unparseable_deadline_falls_back_to_estimate(self):
        items = [{
            "id": 1, "subject": "s", "sender": "bob@y.com", "created_at": NOW,
            "reason_json": {"deadlines": ["ASAP please"]},
        }]
        result = simulate_week(items, [], {}, NOW)
        assert len(result) == 1
        assert result[0]["is_estimated"] is True
        assert result[0]["breaks_at"] == NOW + ESTIMATE_DAYS * DAY

    def test_no_deadline_field_falls_back_to_estimate(self):
        items = [{"id": 1, "subject": "s", "sender": "bob@y.com", "created_at": NOW, "reason_json": {}}]
        result = simulate_week(items, [], {}, NOW)
        assert len(result) == 1
        assert result[0]["is_estimated"] is True

    def test_estimate_beyond_window_is_excluded(self):
        items = [{"id": 1, "subject": "s", "sender": "bob@y.com", "created_at": NOW - 100 * DAY, "reason_json": {}}]
        # created 100 days ago + 3-day estimate is far in the past, not "this week ahead"
        assert simulate_week(items, [], {}, NOW) == []

    def test_missing_created_at_is_excluded_not_crashed(self):
        items = [{"id": 1, "subject": "s", "sender": "bob@y.com", "reason_json": {}}]
        assert simulate_week(items, [], {}, NOW) == []


class TestSimulateWeekWaitingOn:
    def test_due_ts_within_window_is_included(self):
        loops = [{"id": 1, "subject": "s", "contact_email": "bob@y.com", "due_ts": NOW + 2 * DAY}]
        result = simulate_week([], loops, {}, NOW)
        assert len(result) == 1
        assert result[0]["kind"] == "waiting_on"
        assert result[0]["is_estimated"] is False

    def test_due_ts_beyond_window_is_excluded(self):
        loops = [{"id": 1, "subject": "s", "contact_email": "bob@y.com", "due_ts": NOW + 30 * DAY}]
        assert simulate_week([], loops, {}, NOW) == []

    def test_due_ts_in_the_past_is_excluded(self):
        # Already past due -- that's "slipping" (existing /api/now concept),
        # not "will break this week"; the simulation looks forward only.
        loops = [{"id": 1, "subject": "s", "contact_email": "bob@y.com", "due_ts": NOW - DAY}]
        assert simulate_week([], loops, {}, NOW) == []

    def test_no_due_ts_is_excluded_not_crashed(self):
        loops = [{"id": 1, "subject": "s", "contact_email": "bob@y.com"}]
        assert simulate_week([], loops, {}, NOW) == []


class TestSimulateWeekRankingAndSort:
    def test_sorted_soonest_first(self):
        loops = [
            {"id": 1, "subject": "later", "contact_email": "a@y.com", "due_ts": NOW + 5 * DAY},
            {"id": 2, "subject": "sooner", "contact_email": "b@y.com", "due_ts": NOW + 1 * DAY},
        ]
        result = simulate_week([], loops, {}, NOW)
        assert [r["subject"] for r in result] == ["sooner", "later"]

    def test_vip_and_stakes_from_rank_map(self):
        loops = [{"id": 1, "subject": "s", "contact_email": "vip@y.com", "due_ts": NOW + DAY}]
        rank_map = {"vip@y.com": {"rank_score": 90.0, "vip": True}}
        result = simulate_week([], loops, rank_map, NOW)
        assert result[0]["vip"] is True
        assert result[0]["stakes"] == 0.9

    def test_unknown_contact_defaults_to_neutral_stakes(self):
        loops = [{"id": 1, "subject": "s", "contact_email": "stranger@y.com", "due_ts": NOW + DAY}]
        result = simulate_week([], loops, {}, NOW)
        assert result[0]["stakes"] == 0.5
        assert result[0]["vip"] is False

    def test_ties_broken_by_higher_stakes_first(self):
        loops = [
            {"id": 1, "subject": "low", "contact_email": "low@y.com", "due_ts": NOW + DAY},
            {"id": 2, "subject": "high", "contact_email": "high@y.com", "due_ts": NOW + DAY},
        ]
        rank_map = {"low@y.com": {"rank_score": 10.0}, "high@y.com": {"rank_score": 90.0}}
        result = simulate_week([], loops, rank_map, NOW)
        assert [r["subject"] for r in result] == ["high", "low"]

    def test_you_owe_contact_email_parsed_from_sender(self):
        items = [{"id": 1, "subject": "s", "sender": "Bob Smith <bob@y.com>", "created_at": NOW, "reason_json": {}}]
        rank_map = {"bob@y.com": {"rank_score": 80.0, "vip": True}}
        result = simulate_week(items, [], rank_map, NOW)
        assert result[0]["contact_email"] == "bob@y.com"
        assert result[0]["vip"] is True


class TestComputeWeeklySimulationIntegration:
    def test_runs_end_to_end_with_no_data(self, db):
        assert compute_weekly_simulation(db) == []
