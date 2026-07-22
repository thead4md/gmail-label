"""MailMind — deadline -> calendar hold auto-scheduling (client-strategy
reframe §4.4).

thread_analyzer already extracts free-text deadline phrases per message
(predictions.thread_context_json.deadlines); deadline_parser turns the
resolvable ones into a real timestamp. This module proposes a calendar hold
for each one (idempotent -- re-scanning the same email is a no-op) and, for
a contact the user has explicitly opted into auto_calendar_eligible,
creates the event immediately rather than waiting for a human click.

Fully deterministic aggregation + one Calendar API call per eligible hold;
no LLM.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .deadline_parser import parse_deadline_string
from .loops import split_addr
from ..actions.calendar import DEFAULT_DURATION_SECONDS

LOG = logging.getLogger(__name__)

# How far back to scan predictions for a deadline mention.
DEFAULT_LOOKBACK_DAYS = 7


def propose_holds_for_email(
    db,
    email_gmail_id: str,
    account: Optional[str],
    subject: Optional[str],
    deadlines: List[str],
    now_ts: Optional[int] = None,
) -> List[int]:
    """For each deadline phrase that resolves to a real timestamp, propose
    (or return the existing) calendar hold. Unparseable phrases are silently
    skipped -- never a wild guess at a date."""
    from ..storage.queries import create_calendar_hold

    now = now_ts if now_ts is not None else int(time.time())
    hold_ids = []
    for d in deadlines:
        ts = parse_deadline_string(d, now)
        if ts is None:
            continue
        summary = f"Deadline: {subject}" if subject else f"Deadline ({d})"
        hold_id = create_calendar_hold(
            db, account=account, email_gmail_id=email_gmail_id, deadline_text=d,
            summary=summary, start_ts=ts, end_ts=ts + DEFAULT_DURATION_SECONDS,
        )
        hold_ids.append(hold_id)
    return hold_ids


def run_calendar_propose_sweep(
    db,
    calendar_client_for_account: Callable[[Optional[str]], Any],
    account: Optional[str] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    now_ts: Optional[int] = None,
) -> Dict[str, int]:
    """Scan recent predictions with a detected deadline, propose calendar
    holds for each resolvable one, and immediately create the event for
    senders the user has explicitly opted into auto_calendar_eligible.

    ``calendar_client_for_account`` is a callback ``account -> CalendarClient
    | None`` so credential resolution stays the caller's responsibility,
    mirroring loop_radar.run_loop_radar_sweep's executor_for_account.

    Returns {"proposed": newly proposed this sweep, "auto_created": created
    immediately for an eligible sender, "create_failed": auto-create attempt
    failed (e.g. insufficient OAuth scope -- stays 'proposed' for a human to
    retry via the UI)}.
    """
    from ..storage.queries import (
        get_calendar_hold, is_sender_auto_calendar_eligible, update_calendar_hold_status,
    )

    now = now_ts if now_ts is not None else int(time.time())
    since = now - lookback_days * 86400

    account_clause = " AND p.account = ?" if account else ""
    params: tuple = (since, account) if account else (since,)
    rows = db.execute_sql(
        f"""
        SELECT e.gmail_id, e.sender, e.subject, e.account, p.thread_context_json
        FROM predictions p
        JOIN emails e ON e.gmail_id = p.email_gmail_id
        WHERE p.created_at >= ? AND p.thread_context_json IS NOT NULL{account_clause}
        """,
        params,
    ).fetchall()

    counts = {"proposed": 0, "auto_created": 0, "create_failed": 0}
    calendar_clients: Dict[Optional[str], Any] = {}

    for r in rows:
        try:
            ctx = json.loads(r["thread_context_json"]) if r["thread_context_json"] else {}
        except (TypeError, ValueError):
            continue
        deadlines = ctx.get("deadlines") or []
        if not deadlines:
            continue

        row_account = r["account"]
        hold_ids = propose_holds_for_email(db, r["gmail_id"], row_account, r["subject"], deadlines, now_ts=now)

        for hold_id in hold_ids:
            hold = get_calendar_hold(db, hold_id)
            if not hold or hold["status"] != "proposed":
                continue  # already handled on a prior sweep

            counts["proposed"] += 1
            sender_email, _ = split_addr(r["sender"])
            if not is_sender_auto_calendar_eligible(db, sender_email):
                continue  # awaits a human Approve click

            if row_account not in calendar_clients:
                calendar_clients[row_account] = calendar_client_for_account(row_account)
            client = calendar_clients[row_account]
            if client is None:
                continue  # no usable credentials this cycle -- stays 'proposed'

            try:
                event_id = client.create_event(hold["summary"], hold["start_ts"], hold["end_ts"])
            except Exception:
                LOG.warning("Calendar auto-create failed for hold %s", hold_id, exc_info=True)
                event_id = None

            if event_id:
                update_calendar_hold_status(db, hold_id, "created", gcal_event_id=event_id, created_by="auto")
                counts["auto_created"] += 1
            else:
                update_calendar_hold_status(db, hold_id, "create_failed")
                counts["create_failed"] += 1

    return counts
