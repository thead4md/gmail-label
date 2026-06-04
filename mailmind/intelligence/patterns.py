"""Canonical detection patterns shared across features, rules, and channel detection.

This module consolidates regex patterns and domain sets that are used across
multiple classification modules (features.py, rules.py, channels.py) to ensure
consistent matching and reduce maintenance burden.

All patterns support bilingual matching: English + Hungarian (with Unicode case-folding).
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Unsubscribe pattern — combines features.py, rules.py, + channels.py with Hungarian
# ---------------------------------------------------------------------------
UNSUBSCRIBE_RE = re.compile(
    r"(unsubscribe|opt[ -]?out|manage\s+subscriptions?|manage\s+your\s+subscriptions?|"
    r"manage.*preference|email.*preference|"
    r"view.*in.*browser|click here to unsubscribe|list-id|mailing\s+list|stop\s+receiving|"
    # Hungarian
    r"leiratkoz|kiiratkoz"
    r"|nem k[eé]rek t[oö]bb"          # "nem kérek több levelet"
    r"|feliratkoz[aá]s visszavon"      # "feliratkozás visszavonása"
    r"|h[ií]rlev[eé]l"                # "hírlevél" = newsletter
    r"|b[öo]ng[eé]sz[öo]ben megtekint" # "böngészőben megtekintés"
    r"|levéllistá?r[oó]l)",
    re.I | re.UNICODE,
)

# ---------------------------------------------------------------------------
# Calendar pattern — comprehensive version combining features.py + channels.py
# ---------------------------------------------------------------------------
CALENDAR_RE = re.compile(
    r"(calendar-notification@google\.com|@resource\.calendar\.google\.com|"
    r"invitation|invite|meeting|event|calendar|ics|"
    r"\bdeclined\b|\baccepted\b.*\b(event|meeting)|"
    r"napt[aá]r|esem[eé]ny|megh[ií]v[oó])",
    re.I | re.UNICODE,
)

# ---------------------------------------------------------------------------
# Finance pattern — from features.py
# ---------------------------------------------------------------------------
FINANCE_RE = re.compile(
    r'payment|invoice|receipt|transaction|bill|charge',
    re.I
)

# ---------------------------------------------------------------------------
# Finance provider domains — deduplicated (removed 'wise.com' duplicate,
# 'transferwise.com' deprecated)
# ---------------------------------------------------------------------------
FINANCE_DOMAINS = {
    'paypal.com',
    'stripe.com',
    'revolut.com',
    'otp.hu',
    'wise.com',
    'n26.com',
}
