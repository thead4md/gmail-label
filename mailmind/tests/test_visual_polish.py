"""Pass 12B + visual polish: sparkline + CSS presence."""
from __future__ import annotations

from mailmind.dashboard.helpers import confidence_sparkline_html
from mailmind.dashboard.theme import _CSS


def test_sparkline_needs_two_points():
    assert confidence_sparkline_html({"score": 70}) == ""


def test_sparkline_renders_with_two_points():
    html = confidence_sparkline_html({"score": 70, "ml_confidence": 0.8})
    assert "<svg" in html and "polyline" not in html  # uses path
    assert "<path" in html


def test_sparkline_three_points_has_three_dots():
    html = confidence_sparkline_html(
        {"score": 70, "ml_confidence": 0.8, "llm_confidence": 0.9})
    assert html.count("<circle") == 3


def test_sparkline_none_safe():
    assert confidence_sparkline_html(None) == ""


def test_css_has_pulse_animation():
    assert "@keyframes mm-pulse" in _CSS


def test_css_has_mobile_media_query():
    assert "@media (max-width: 768px)" in _CSS
