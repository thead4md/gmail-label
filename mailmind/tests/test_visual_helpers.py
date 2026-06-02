"""Tests for visual HTML helper functions (dashboard/helpers.py additions)."""
from __future__ import annotations

import pytest
from mailmind.dashboard.helpers import (
    confidence_bar_html,
    label_chip_html,
    channel_chip_html,
    sender_avatar_html,
    trust_badge_html,
    reply_needed_pill_html,
    email_card_html,
)
from mailmind.dashboard.theme import label_color, channel_color, trust_color


# ---------------------------------------------------------------------------
# label_color
# ---------------------------------------------------------------------------

def test_label_color_known():
    assert label_color("URGENT") == "#FF4757"
    assert label_color("WORK")   == "#5B8AF0"

def test_label_color_case_insensitive():
    assert label_color("urgent") == label_color("URGENT")

def test_label_color_unknown_returns_default():
    c = label_color("TOTALLY_UNKNOWN_LABEL")
    assert c.startswith("#")   # returns some hex colour


# ---------------------------------------------------------------------------
# channel_color / trust_color
# ---------------------------------------------------------------------------

def test_channel_color_known():
    assert channel_color("newsletter") == "#9B6DFF"
    assert channel_color("team")       == "#5B8AF0"

def test_trust_color_known():
    assert trust_color("trusted")   == "#2ED573"
    assert trust_color("watchlist") == "#FF4757"


# ---------------------------------------------------------------------------
# sender_avatar_html
# ---------------------------------------------------------------------------

def test_avatar_contains_initial_from_name():
    html = sender_avatar_html("Alice Smith <alice@example.com>")
    assert "A" in html

def test_avatar_handles_email_only():
    html = sender_avatar_html("bob@example.com")
    assert "B" in html

def test_avatar_handles_none():
    html = sender_avatar_html(None)
    assert "?" in html

def test_avatar_is_html():
    html = sender_avatar_html("Carol")
    assert "<div" in html and "mm-avatar" in html


# ---------------------------------------------------------------------------
# label_chip_html
# ---------------------------------------------------------------------------

def test_label_chip_contains_label_text():
    html = label_chip_html("URGENT")
    assert "URGENT" in html

def test_label_chip_uses_urgent_color():
    html = label_chip_html("URGENT")
    assert "#FF4757" in html

def test_label_chip_none_safe():
    html = label_chip_html(None)
    assert "<span" in html   # returns something, no crash


# ---------------------------------------------------------------------------
# channel_chip_html
# ---------------------------------------------------------------------------

def test_channel_chip_contains_channel():
    html = channel_chip_html("newsletter")
    assert "newsletter" in html.lower()

def test_channel_chip_has_icon():
    html = channel_chip_html("team")
    assert "👥" in html

def test_channel_chip_unknown_safe():
    html = channel_chip_html("unknown")
    assert "<span" in html


# ---------------------------------------------------------------------------
# confidence_bar_html
# ---------------------------------------------------------------------------

def test_confidence_bar_green_above_08():
    html = confidence_bar_html(0.85)
    assert "#2ED573" in html   # green

def test_confidence_bar_amber_mid():
    html = confidence_bar_html(0.65)
    assert "#FFA502" in html   # amber

def test_confidence_bar_red_below_05():
    html = confidence_bar_html(0.3)
    assert "#FF4757" in html   # red

def test_confidence_bar_shows_percent():
    html = confidence_bar_html(0.75)
    assert "75%" in html

def test_confidence_bar_none_safe():
    html = confidence_bar_html(None)
    assert "0%" in html        # renders 0 without crash


# ---------------------------------------------------------------------------
# trust_badge_html
# ---------------------------------------------------------------------------

def test_trust_badge_trusted():
    html = trust_badge_html("trusted")
    assert "trusted" in html
    assert "#2ED573" in html

def test_trust_badge_watchlist():
    html = trust_badge_html("watchlist")
    assert "watchlist" in html
    assert "#FF4757" in html

def test_trust_badge_none_defaults_neutral():
    html = trust_badge_html(None)
    assert "neutral" in html


# ---------------------------------------------------------------------------
# reply_needed_pill_html
# ---------------------------------------------------------------------------

def test_reply_needed_pill():
    html = reply_needed_pill_html()
    assert "Reply needed" in html
    assert "mm-pill-reply" in html


# ---------------------------------------------------------------------------
# email_card_html (integration)
# ---------------------------------------------------------------------------

def test_email_card_contains_subject():
    html = email_card_html(
        subject="Project update",
        sender="alice@co.com",
        time_ago="5 min ago",
    )
    assert "Project update" in html

def test_email_card_shows_reply_needed():
    html = email_card_html(
        subject="Q",
        sender="b@c.com",
        time_ago="now",
        reply_needed=True,
    )
    assert "Reply needed" in html

def test_email_card_no_reply_needed_omits_pill():
    html = email_card_html(
        subject="Q",
        sender="b@c.com",
        time_ago="now",
        reply_needed=False,
    )
    assert "Reply needed" not in html

def test_email_card_includes_thread_summary():
    html = email_card_html(
        subject="Q",
        sender="b@c.com",
        time_ago="now",
        thread_summary="Waiting on sign-off",
    )
    assert "Waiting on sign-off" in html

def test_email_card_is_html_div():
    html = email_card_html("S", "s@e.com", "now")
    assert "mm-card" in html
    assert "<div" in html
