"""Deadline -> calendar hold creation (client-strategy reframe §4.4).

Deliberately a thin, single-purpose client -- unlike labels/star/archive
(ActionExecutor), a calendar hold is proposed ONCE per detected deadline and
created with a SINGLE explicit human approval (or, for a contact the user
has explicitly opted in via auto_calendar_eligible, created immediately).
Two states rather than the drafts flow's three-step gate: composing and
irreversibly SENDING new content to a real person (drafts) is a materially
different risk than creating a calendar hold, which the user can trivially
edit or delete afterward with no external party involved.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

from googleapiclient.errors import HttpError

from .safety import SafetyPolicy

LOG = logging.getLogger(__name__)

DEFAULT_DURATION_SECONDS = 30 * 60  # a 30-minute hold, matching a typical calendar block


class CalendarClient:
    """Safe Google Calendar event creator.

    Mirrors ActionExecutor.send_message's contract: dry_run is checked
    FIRST and unconditionally (no way to reach the real API call while
    True), and every failure mode (rate limit, HttpError, unexpected
    exception -- including insufficient OAuth scope on a not-yet-re-consented
    token) returns None rather than raising, so a calendar failure can never
    take down the watch loop or the API request handling it.
    """

    def __init__(
        self,
        service,
        safety_policy: SafetyPolicy,
        calendar_id: str = "primary",
        rate_limit_seconds: float = 0.3,
    ):
        self.service = service
        self.safety_policy = safety_policy
        self.calendar_id = calendar_id
        self.rate_limit_seconds = rate_limit_seconds

    def create_event(
        self,
        summary: str,
        start_ts: int,
        end_ts: Optional[int] = None,
        description: Optional[str] = None,
    ) -> Optional[str]:
        """Create a calendar hold. Returns the created event id, the literal
        string "dry_run" in dry-run mode, or None on any failure/refusal."""
        if self.safety_policy.dry_run:
            LOG.info(f"DRY RUN: would create calendar event {summary!r} at {start_ts}")
            return "dry_run"

        if self.safety_policy._is_rate_limited():
            LOG.warning(
                f"Calendar create for {summary!r} refused: rate limit exceeded "
                f"({self.safety_policy.max_actions_per_hour} actions/hour)"
            )
            return None

        if end_ts is None:
            end_ts = start_ts + DEFAULT_DURATION_SECONDS

        try:
            start_dt = datetime.fromtimestamp(start_ts).astimezone()
            end_dt = datetime.fromtimestamp(end_ts).astimezone()
            body = {
                "summary": summary,
                "description": description or "",
                "start": {"dateTime": start_dt.isoformat()},
                "end": {"dateTime": end_dt.isoformat()},
            }
            created = self.service.events().insert(calendarId=self.calendar_id, body=body).execute()
            event_id = created.get("id") if created else None
            LOG.info(f"Created calendar event {summary!r} (id: {event_id})")
            time.sleep(self.rate_limit_seconds)
            return event_id
        except HttpError as e:
            LOG.error(f"Calendar API error creating event {summary!r}: {e}")
            return None
        except Exception as e:
            LOG.error(f"Unexpected error creating calendar event {summary!r}: {e}", exc_info=True)
            return None
