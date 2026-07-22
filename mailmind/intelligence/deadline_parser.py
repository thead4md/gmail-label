"""MailMind — best-effort deadline-string → timestamp parser.

`thread_analyzer._DEADLINE_RE` already extracts free-text deadline phrases
("by Friday", "péntekig", "2026.06.15", "3-án", "EOD") as raw strings for
display. This module turns those same phrases into an actual epoch
timestamp where it can do so with reasonable confidence, so two V3 features
can build on real dates instead of opaque text: inbox simulation
(intelligence/simulation.py, "what breaks this week") and deadline→calendar
auto-scheduling (actions/calendar.py).

Deliberately conservative: returns None rather than guessing when a phrase
doesn't match a known, resolvable pattern (e.g. "jövő hét" / "next week" is
vague, so it resolves to "end of next week" as a documented approximation --
never a wild guess for something genuinely ambiguous like "soon").

Bilingual (English + Hungarian), matching thread_analyzer's vocabulary.
Timezone convention matches the rest of this codebase's _day_start_ts()
helpers (draft_reply.py, loop_radar.py, now.py): local system time via
datetime.now().astimezone(), not UTC-normalized.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

_WEEKDAYS_EN = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_WEEKDAYS_HU = {
    "hétfő": 0, "hetfo": 0, "kedd": 1, "szerda": 2,
    "csütörtök": 3, "csutortok": 3, "péntek": 4, "pentek": 4,
    "szombat": 5, "vasárnap": 6, "vasarnap": 6,
}

_ISO_DATE_RE = re.compile(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})")
_HU_ORDINAL_DATE_RE = re.compile(r"\b(\d{1,2})-[aáeé]n\b", re.I | re.UNICODE)
_WEEKDAY_RE = re.compile(
    # Hungarian commonly suffixes the weekday stem directly with "-ig"
    # ("péntekig" = "until Friday", no space) -- allow an optional "ig"
    # before the boundary so that form matches too.
    r"\b(" + "|".join(list(_WEEKDAYS_EN) + list(_WEEKDAYS_HU)) + r")(ig)?\b",
    re.I | re.UNICODE,
)
_TOMORROW_RE = re.compile(r"\b(tomorrow|holnaput[aá]n|holnap)\b", re.I | re.UNICODE)
_EOD_RE = re.compile(r"\b(end of day|eod)\b", re.I)
_NEXT_WEEK_RE = re.compile(r"j[oö]v[oő] h[eé]t", re.I | re.UNICODE)


def _end_of_day(dt: datetime) -> datetime:
    return dt.replace(hour=23, minute=59, second=59, microsecond=0)


def _next_weekday(now: datetime, target_weekday: int) -> datetime:
    """Nearest occurrence of *target_weekday* at or after today (0 days
    ahead if today already is that weekday -- "by Friday" said on a Friday
    means today, not a week from now)."""
    days_ahead = (target_weekday - now.weekday()) % 7
    return _end_of_day(now + timedelta(days=days_ahead))


def parse_deadline_string(text: Optional[str], now_ts: Optional[int] = None) -> Optional[int]:
    """Best-effort epoch timestamp for a free-text deadline phrase, or None.

    Tries patterns in order of confidence: explicit ISO/dotted date first,
    then the Hungarian day-of-month ordinal, then a named weekday, then
    tomorrow/day-after-tomorrow, then "end of day", then a vague "next week"
    approximation. Returns None for anything that matches none of these --
    deliberately never guesses at an ungrounded phrase like "soon".
    """
    if not text:
        return None
    now = (
        datetime.fromtimestamp(now_ts).astimezone()
        if now_ts is not None
        else datetime.now().astimezone()
    )

    m = _ISO_DATE_RE.search(text)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return int(_end_of_day(now.replace(year=year, month=month, day=day)).timestamp())
        except ValueError:
            pass  # fall through to other patterns on an out-of-range date

    m = _HU_ORDINAL_DATE_RE.search(text)
    if m:
        day = int(m.group(1))
        if 1 <= day <= 31:
            candidate = now.replace(day=1)
            try:
                candidate = candidate.replace(day=day)
            except ValueError:
                candidate = None  # e.g. day=31 in a 30-day month
            if candidate is not None:
                if candidate.date() < now.date():
                    # Already passed this month -- roll to next month.
                    next_month = (now.month % 12) + 1
                    next_year = now.year + (1 if now.month == 12 else 0)
                    try:
                        candidate = candidate.replace(year=next_year, month=next_month, day=day)
                    except ValueError:
                        candidate = None
                if candidate is not None:
                    return int(_end_of_day(candidate).timestamp())

    m = _WEEKDAY_RE.search(text)
    if m:
        name = m.group(1).lower()
        target = _WEEKDAYS_EN.get(name, _WEEKDAYS_HU.get(name))
        if target is not None:
            return int(_next_weekday(now, target).timestamp())

    m = _TOMORROW_RE.search(text)
    if m:
        word = m.group(1).lower()
        days = 2 if word in ("holnaputan", "holnaputána", "holnapután") else 1
        return int(_end_of_day(now + timedelta(days=days)).timestamp())

    if _EOD_RE.search(text):
        return int(_end_of_day(now).timestamp())

    if _NEXT_WEEK_RE.search(text):
        # Vague by nature -- approximate as "end of next week" (the Friday
        # that starts the week after this one), documented, never a wild guess.
        days_to_friday = (4 - now.weekday()) % 7
        return int(_end_of_day(now + timedelta(days=days_to_friday + 7)).timestamp())

    return None
