"""Map a label name to a Gmail-allowed colour, deterministically.

Gmail's labels API only accepts colours from a fixed palette; arbitrary hex
values are rejected with HTTP 400. This module exposes a curated set of
known-valid {backgroundColor, textColor} pairs and a stable hash that assigns
each label name the same colour on every run (so the Gmail label colours line
up with the dashboard's per-label colouring conceptually — one colour per label).
"""
from __future__ import annotations

from typing import Dict, Optional

# Known-valid Gmail label colour pairs (background from Gmail's allowed palette,
# white text for contrast). Kept saturated and well-separated.
GMAIL_LABEL_PALETTE = [
    {"backgroundColor": "#fb4c2f", "textColor": "#ffffff"},  # red
    {"backgroundColor": "#ffad47", "textColor": "#ffffff"},  # orange
    {"backgroundColor": "#16a766", "textColor": "#ffffff"},  # green
    {"backgroundColor": "#4a86e8", "textColor": "#ffffff"},  # blue
    {"backgroundColor": "#a479e2", "textColor": "#ffffff"},  # purple
    {"backgroundColor": "#f691b3", "textColor": "#ffffff"},  # pink
    {"backgroundColor": "#2da2bb", "textColor": "#ffffff"},  # cyan
    {"backgroundColor": "#0b804b", "textColor": "#ffffff"},  # dark green
    {"backgroundColor": "#8a1c0a", "textColor": "#ffffff"},  # dark red
    {"backgroundColor": "#41236d", "textColor": "#ffffff"},  # indigo
    {"backgroundColor": "#674ea7", "textColor": "#ffffff"},  # violet
    {"backgroundColor": "#0d3472", "textColor": "#ffffff"},  # navy
    {"backgroundColor": "#076239", "textColor": "#ffffff"},  # forest
    {"backgroundColor": "#b65775", "textColor": "#ffffff"},  # rose
]

# Optional curated overrides for common categories (stable, recognisable colours).
_CURATED: Dict[str, Dict[str, str]] = {
    "NEWSLETTER":   {"backgroundColor": "#a479e2", "textColor": "#ffffff"},
    "NOTIFICATION": {"backgroundColor": "#999999", "textColor": "#ffffff"},
    "MASS_EMAIL":   {"backgroundColor": "#f691b3", "textColor": "#ffffff"},
    "FINANCE":      {"backgroundColor": "#16a766", "textColor": "#ffffff"},
    "CALENDAR":     {"backgroundColor": "#2da2bb", "textColor": "#ffffff"},
    "PERSONAL":     {"backgroundColor": "#ffad47", "textColor": "#ffffff"},
    "ACTION_REQUIRED": {"backgroundColor": "#fb4c2f", "textColor": "#ffffff"},
}


def _fnv(key: str) -> int:
    h = 2166136261
    for ch in key:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h


def gmail_color_for(label: str) -> Optional[Dict[str, str]]:
    """Return a Gmail colour dict for a label name (None for empty input).

    Matches on the bare category — strips a 'Prefix/' and upper-cases — so
    'MailMind/Finance' and 'FINANCE' resolve to the same colour.
    """
    if not label:
        return None
    bare = label.split("/")[-1].strip().upper().replace(" ", "_")
    if bare in _CURATED:
        return _CURATED[bare]
    return GMAIL_LABEL_PALETTE[_fnv(bare) % len(GMAIL_LABEL_PALETTE)]
