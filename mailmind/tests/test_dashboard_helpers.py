"""Tests for dashboard helper functions.

Covers filter_now_items, get_time_ago_str, format_unix_ts,
get_confidence_badge, and parse_reason_json.
"""
from __future__ import annotations

import json

from mailmind.dashboard.helpers import (
    filter_now_items,
    format_unix_ts,
    get_confidence_badge,
    get_time_ago_str,
    kpi_card_html,
    parse_reason_json,
)
from mailmind.processing.queue_manager import QueueManager


class TestKpiCardHtml:
    """Tests for the NOW-tab KPI overview card grid."""

    def test_empty_cards_returns_empty_string(self):
        assert kpi_card_html([]) == ""

    def test_renders_label_value_and_grid(self):
        html_out = kpi_card_html([{"icon": "📨", "label": "Triaged today", "value": 12}])
        assert "mm-kpi-grid" in html_out
        assert "mm-kpi-card" in html_out
        assert "Triaged today" in html_out
        assert ">12<" in html_out

    def test_positive_delta_is_up_and_green_class(self):
        html_out = kpi_card_html([{"label": "x", "value": 5, "delta": 3}])
        assert "mm-kpi-delta-up" in html_out
        assert "+3 vs yesterday" in html_out

    def test_negative_delta_is_down_class(self):
        html_out = kpi_card_html([{"label": "x", "value": 5, "delta": -2}])
        assert "mm-kpi-delta-down" in html_out
        assert "-2 vs yesterday" in html_out

    def test_zero_delta_is_flat(self):
        html_out = kpi_card_html([{"label": "x", "value": 5, "delta": 0}])
        assert "mm-kpi-delta-flat" in html_out

    def test_no_delta_key_renders_no_delta_line(self):
        html_out = kpi_card_html([{"label": "x", "value": 5}])
        assert "mm-kpi-delta" not in html_out

    def test_label_is_html_escaped(self):
        html_out = kpi_card_html([{"label": "<script>", "value": 1}])
        assert "<script>" not in html_out
        assert "&lt;script&gt;" in html_out


class TestFilterNowItems:
    """Tests for filter_now_items helper function."""

    def test_filters_by_reply_needed(self):
        """Test that items with reply_needed=True are included."""
        items = [
            {
                'id': 1,
                'priority_score': 30,
                'reason_json': {'reply_needed': True},
                'created_at': 100,
            },
            {
                'id': 2,
                'priority_score': 40,
                'reason_json': {'reply_needed': False},
                'created_at': 101,
            },
        ]

        result = filter_now_items(items)
        assert len(result) == 1
        assert result[0]['id'] == 1

    def test_filters_by_priority_score(self):
        """Test that items with priority_score above threshold are included."""
        items = [
            {
                'id': 1,
                'priority_score': 80,  # Above threshold (65)
                'reason_json': {'reply_needed': False},
                'created_at': 100,
            },
            {
                'id': 2,
                'priority_score': 40,  # Below threshold
                'reason_json': {'reply_needed': False},
                'created_at': 101,
            },
        ]

        result = filter_now_items(items, queue_threshold=0.65)
        assert len(result) == 1
        assert result[0]['id'] == 1

    def test_includes_items_meeting_either_criteria(self):
        """Test that items meeting either reply_needed OR priority criteria are included."""
        items = [
            {
                'id': 1,
                'priority_score': 80,  # High priority
                'reason_json': {'reply_needed': False},
                'created_at': 100,
            },
            {
                'id': 2,
                'priority_score': 30,  # Low priority
                'reason_json': {'reply_needed': True},  # But reply needed
                'created_at': 101,
            },
            {
                'id': 3,
                'priority_score': 40,  # Low priority
                'reason_json': {'reply_needed': False},  # No reply needed
                'created_at': 102,
            },
        ]

        result = filter_now_items(items, queue_threshold=0.65)
        assert len(result) == 2
        ids = {item['id'] for item in result}
        assert ids == {1, 2}

    def test_sorts_by_priority_desc_then_created_asc(self):
        """Test that results are sorted by priority_score DESC, created_at ASC."""
        items = [
            {
                'id': 1,
                'priority_score': 50,
                'reason_json': {'reply_needed': True},
                'created_at': 200,
            },
            {
                'id': 2,
                'priority_score': 80,
                'reason_json': {'reply_needed': True},
                'created_at': 100,
            },
            {
                'id': 3,
                'priority_score': 80,
                'reason_json': {'reply_needed': True},
                'created_at': 50,
            },
        ]

        result = filter_now_items(items)
        # Should be sorted by priority DESC: 80, 80, 50
        # Then by created_at ASC: for priority 80, created_at 50 before 100
        assert result[0]['id'] == 3  # priority 80, created 50
        assert result[1]['id'] == 2  # priority 80, created 100
        assert result[2]['id'] == 1  # priority 50, created 200

    def test_handles_reason_json_as_string(self):
        """Test that reason_json as JSON string is properly parsed."""
        items = [
            {
                'id': 1,
                'priority_score': 30,
                'reason_json': json.dumps({'reply_needed': True}),  # JSON string
                'created_at': 100,
            },
        ]

        result = filter_now_items(items)
        assert len(result) == 1
        assert result[0]['id'] == 1

    def test_handles_malformed_reason_json(self):
        """Test that malformed reason_json is handled gracefully."""
        items = [
            {
                'id': 1,
                'priority_score': 80,  # High priority
                'reason_json': 'invalid json {',
                'created_at': 100,
            },
        ]

        result = filter_now_items(items, queue_threshold=0.65)
        # Should still include due to high priority
        assert len(result) == 1

    def test_empty_list_returns_empty(self):
        """Test that empty item list returns empty result."""
        result = filter_now_items([])
        assert result == []

    def test_uses_default_threshold_if_not_provided(self):
        """Test that default queue threshold is used if not provided."""
        items = [
            {
                'id': 1,
                'priority_score': 70,  # Above default threshold
                'reason_json': {},
                'created_at': 100,
            },
        ]

        result = filter_now_items(items)  # No threshold provided
        assert len(result) == 1


# ---------------------------------------------------------------------------
# parse_reason_json
# ---------------------------------------------------------------------------

class TestParseReasonJson:
    def test_dict_passthrough(self):
        d = {'reply_needed': True, 'score': 80}
        assert parse_reason_json(d) == d

    def test_json_string_parsed(self):
        s = json.dumps({'reply_needed': False, 'trust_tier': 'trusted'})
        result = parse_reason_json(s)
        assert result['trust_tier'] == 'trusted'

    def test_none_returns_empty(self):
        assert parse_reason_json(None) == {}

    def test_invalid_json_returns_empty(self):
        assert parse_reason_json("not valid {json}") == {}

    def test_non_dict_json_returns_empty(self):
        # Valid JSON but not a dict (e.g. a list)
        assert parse_reason_json("[1, 2, 3]") == {}


# ---------------------------------------------------------------------------
# get_confidence_badge
# ---------------------------------------------------------------------------

class TestGetConfidenceBadge:
    def test_high_confidence_green(self):
        assert get_confidence_badge(0.9) == "🟢"

    def test_medium_confidence_amber(self):
        assert get_confidence_badge(0.7) == "🟡"

    def test_low_confidence_red(self):
        assert get_confidence_badge(0.3) == "🔴"

    def test_boundary_above_08_green(self):
        assert get_confidence_badge(0.81) == "🟢"

    def test_boundary_exactly_08_amber(self):
        # 0.8 is NOT > 0.8, so amber
        assert get_confidence_badge(0.8) == "🟡"

    def test_boundary_exactly_05_red(self):
        # 0.5 is NOT > 0.5, so red
        assert get_confidence_badge(0.5) == "🔴"

    def test_none_returns_white(self):
        assert get_confidence_badge(None) == "⚪"


# ---------------------------------------------------------------------------
# get_time_ago_str
# ---------------------------------------------------------------------------

class TestGetTimeAgoStr:
    def test_none_returns_never(self):
        assert get_time_ago_str(None) == "Never"

    def test_zero_returns_never(self):
        assert get_time_ago_str(0) == "Never"

    def test_under_one_minute(self):
        import time
        ts = int(time.time()) - 30
        assert get_time_ago_str(ts) == "< 1 min ago"

    def test_minutes_ago(self):
        import time
        ts = int(time.time()) - 120  # 2 minutes
        assert get_time_ago_str(ts) == "2 min ago"

    def test_hours_ago(self):
        import time
        ts = int(time.time()) - 7200  # 2 hours
        assert get_time_ago_str(ts) == "2h ago"

    def test_days_ago(self):
        import time
        ts = int(time.time()) - 172800  # 2 days
        assert get_time_ago_str(ts) == "2d ago"


# ---------------------------------------------------------------------------
# format_unix_ts
# ---------------------------------------------------------------------------

class TestFormatUnixTs:
    def test_none_returns_dash(self):
        assert format_unix_ts(None) == "—"

    def test_zero_returns_dash(self):
        assert format_unix_ts(0) == "—"

    def test_known_timestamp(self):
        # 2024-01-15 00:00:00 UTC
        ts = 1705276800
        result = format_unix_ts(ts)
        assert "2024-01-15" in result

