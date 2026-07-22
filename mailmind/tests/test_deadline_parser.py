"""Tests for intelligence/deadline_parser.py: best-effort deadline-string ->
epoch-timestamp parsing, shared by inbox simulation and calendar
auto-scheduling.

Expected values are computed via datetime arithmetic in the tests
themselves (never hardcoded assumptions about what weekday a given
calendar date falls on), so these tests are independent of when they run.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from mailmind.intelligence.deadline_parser import parse_deadline_string

# A fixed, arbitrary reference moment for every test (not "now()" -- keeps
# tests deterministic regardless of when the suite runs).
_REF = datetime(2026, 7, 22, 10, 0, 0).astimezone()
_REF_TS = int(_REF.timestamp())


def _end_of_day_ts(dt: datetime) -> int:
    return int(dt.replace(hour=23, minute=59, second=59, microsecond=0).timestamp())


class TestNoMatch:
    def test_none_input(self):
        assert parse_deadline_string(None, _REF_TS) is None

    def test_empty_string(self):
        assert parse_deadline_string("", _REF_TS) is None

    def test_unparseable_vague_phrase(self):
        assert parse_deadline_string("Please get to this soon", _REF_TS) is None


class TestIsoDates:
    def test_dotted_date(self):
        result = parse_deadline_string("Please send by 2026.08.15", _REF_TS)
        assert result == _end_of_day_ts(datetime(2026, 8, 15).astimezone())

    def test_dash_date(self):
        result = parse_deadline_string("due 2026-12-01", _REF_TS)
        assert result == _end_of_day_ts(datetime(2026, 12, 1).astimezone())

    def test_out_of_range_date_falls_through_to_none(self):
        # Feb 30 doesn't exist; must not raise, must not fabricate a date.
        assert parse_deadline_string("by 2026.02.30", _REF_TS) is None


class TestHungarianOrdinalDate:
    def test_day_still_ahead_this_month(self):
        # REF is July 22; the 28th hasn't happened yet this month.
        result = parse_deadline_string("kérem a jelentést 28-án", _REF_TS)
        assert result == _end_of_day_ts(datetime(2026, 7, 28).astimezone())

    def test_day_already_passed_rolls_to_next_month(self):
        # REF is July 22; the 3rd already passed this month -> August 3.
        result = parse_deadline_string("kérem 3-án", _REF_TS)
        assert result == _end_of_day_ts(datetime(2026, 8, 3).astimezone())

    def test_en_suffix_variant(self):
        result = parse_deadline_string("legyen kész 5-én", _REF_TS)
        assert result == _end_of_day_ts(datetime(2026, 8, 5).astimezone())


class TestWeekdayNames:
    def test_english_weekday_resolves_to_next_occurrence(self):
        result = parse_deadline_string("Please reply by Friday", _REF_TS)
        target = 4  # Friday
        days_ahead = (target - _REF.weekday()) % 7
        assert result == _end_of_day_ts(_REF + timedelta(days=days_ahead))

    def test_today_is_the_named_weekday_means_today(self):
        # If REF's own weekday is named, "by <today's weekday>" must mean
        # today (0 days ahead), not a week from now.
        weekday_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        today_name = weekday_names[_REF.weekday()]
        result = parse_deadline_string(f"by {today_name}", _REF_TS)
        assert result == _end_of_day_ts(_REF)

    def test_hungarian_weekday(self):
        result = parse_deadline_string("péntekig kérem elküldeni", _REF_TS)
        target = 4  # péntek = Friday
        days_ahead = (target - _REF.weekday()) % 7
        assert result == _end_of_day_ts(_REF + timedelta(days=days_ahead))


class TestRelativeDays:
    def test_tomorrow_english(self):
        result = parse_deadline_string("Can you send this tomorrow?", _REF_TS)
        assert result == _end_of_day_ts(_REF + timedelta(days=1))

    def test_holnap_hungarian(self):
        result = parse_deadline_string("holnap kérem", _REF_TS)
        assert result == _end_of_day_ts(_REF + timedelta(days=1))

    def test_day_after_tomorrow_hungarian(self):
        result = parse_deadline_string("holnapután lesz kész", _REF_TS)
        assert result == _end_of_day_ts(_REF + timedelta(days=2))


class TestEndOfDay:
    def test_eod_abbreviation(self):
        result = parse_deadline_string("Need this EOD", _REF_TS)
        assert result == _end_of_day_ts(_REF)

    def test_end_of_day_phrase(self):
        result = parse_deadline_string("by end of day please", _REF_TS)
        assert result == _end_of_day_ts(_REF)


class TestNextWeekApproximation:
    def test_jovo_het_resolves_to_a_future_friday(self):
        result = parse_deadline_string("jövő héten végzünk vele", _REF_TS)
        assert result is not None
        resolved = datetime.fromtimestamp(result).astimezone()
        assert resolved.weekday() == 4  # Friday
        assert resolved > _REF + timedelta(days=6)  # genuinely "next" week, not this one


class TestPriorityOrder:
    def test_iso_date_wins_over_weekday_mention_in_same_string(self):
        # Both an ISO date and a weekday name appear; the explicit date
        # should win as the higher-confidence signal.
        result = parse_deadline_string("by Friday, 2026.09.10 at the latest", _REF_TS)
        assert result == _end_of_day_ts(datetime(2026, 9, 10).astimezone())

    def test_defaults_now_ts_to_current_time_when_omitted(self):
        # Just assert it doesn't crash and returns a plausible (future-ish)
        # timestamp when now_ts isn't supplied.
        result = parse_deadline_string("by Friday")
        assert result is not None
