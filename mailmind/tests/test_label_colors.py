"""Tests for label colouring — dashboard (theme) and Gmail palette mapping."""
from __future__ import annotations

import re

import pytest

from mailmind.ingestion.gmail_label_colors import (
    gmail_color_for,
    GMAIL_LABEL_PALETTE,
)

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


# ---------------------------------------------------------------------------
# Gmail palette mapping (pure, no external deps)
# ---------------------------------------------------------------------------

def test_gmail_color_none_for_empty():
    assert gmail_color_for("") is None
    assert gmail_color_for(None) is None


def test_gmail_color_is_valid_palette_pair():
    c = gmail_color_for("OE")
    assert c in GMAIL_LABEL_PALETTE or "backgroundColor" in c
    assert _HEX.match(c["backgroundColor"])
    assert _HEX.match(c["textColor"])


def test_gmail_color_stable():
    assert gmail_color_for("hirdetes-l") == gmail_color_for("hirdetes-l")


def test_gmail_color_strips_prefix_and_case():
    # 'MailMind/Finance' and 'FINANCE' resolve to the same colour.
    assert gmail_color_for("MailMind/Finance") == gmail_color_for("FINANCE")


def test_gmail_color_curated_finance():
    assert gmail_color_for("FINANCE") == {"backgroundColor": "#16a766", "textColor": "#ffffff"}


def test_gmail_color_distinct_for_different_labels():
    labels = ["OE", "HIRDETES-L", "INFO-L", "811/BCS", "NEWSLETTER", "FINANCE"]
    colors = {gmail_color_for(l)["backgroundColor"] for l in labels}
    assert len(colors) >= 4  # well-separated, not all identical


# ---------------------------------------------------------------------------
# Dashboard theme colouring (skips if streamlit isn't installed)
# ---------------------------------------------------------------------------

def test_theme_label_color_hex_and_stable():
    pytest.importorskip("streamlit")
    from mailmind.dashboard.theme import label_color, LABEL_COLORS

    # Curated label keeps its semantic colour.
    assert label_color("FINANCE") == LABEL_COLORS["FINANCE"]
    # Unknown label gets a stable hex colour (hashed), not the bare default-for-all.
    a1 = label_color("hirdetes-l")
    a2 = label_color("hirdetes-l")
    assert a1 == a2
    assert _HEX.match(a1)
    # Two unrelated unknown labels should usually differ.
    assert label_color("Spiritual_Reflections") != label_color("Youth_Council")
