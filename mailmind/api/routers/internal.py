"""Internal, machine-to-machine endpoints.

Unlike every other router in this package, these are NOT part of the
browser-facing dashboard API and are NOT gated by the cookie-based
`require_auth` (see mailmind.api.auth) — there is no human session here.
Instead they're guarded by a shared-secret header, `X-Poll-Secret`, checked
against the `MAILMIND_POLL_SECRET_TOKEN` env var.

This exists for MAILMIND_RUN_MODE=external (see fly-start.sh / fly.toml):
when the Fly machine scales to zero between requests, there is no long-lived
`run --watch` process left polling Gmail every MAILMIND_POLL_SECONDS. Instead
an external trigger (see .github/workflows/poll-trigger.yml) calls
POST /internal/poll on this same FastAPI process — which Fly will cold-start
if the machine is stopped — to run exactly one watch-loop cycle.
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from mailmind.actions.safety import SafetyPolicy
from mailmind.api.deps import get_db
from mailmind.main import (
    _maybe_detect_loops,
    _maybe_mirror_mailbox,
    _maybe_propose_calendar_holds,
    _maybe_prune,
    _maybe_refresh_labels,
    _maybe_retrain,
    _maybe_run_loop_radar,
    _maybe_send_scheduled_drafts,
    _maybe_suggest_labels,
    _maybe_unsnooze,
    _record_heartbeat,
    _run_all_accounts,
)

LOG = logging.getLogger(__name__)

POLL_SECRET_HEADER = "X-Poll-Secret"
POLL_SECRET_ENV_VAR = "MAILMIND_POLL_SECRET_TOKEN"

router = APIRouter(prefix="/internal", tags=["internal"])


def _check_poll_secret(
    x_poll_secret: Optional[str] = Header(default=None, alias=POLL_SECRET_HEADER),
) -> None:
    """Shared-secret guard for /internal/poll.

    - 503 if MAILMIND_POLL_SECRET_TOKEN itself is unset: an empty/unset
      server-side secret must never be treated as "anything goes" — the
      endpoint is unusable (safely) until an operator configures it.
    - 401 if the header is missing or doesn't match (constant-time compare).
    """
    expected = os.environ.get(POLL_SECRET_ENV_VAR, "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=f"{POLL_SECRET_ENV_VAR} is not configured on the server.",
        )
    if not x_poll_secret or not hmac.compare_digest(x_poll_secret, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Poll-Secret.")


@router.post("/poll", dependencies=[Depends(_check_poll_secret)])
def run_one_poll_cycle() -> dict:
    """Run exactly one cycle of the watch loop's while-True body.

    Reuses the same functions run().--watch calls every ~120s (see main.py) —
    nothing here is re-implemented. A shared, db-backed SafetyPolicy is built
    for this cycle so the (now-persisted) rate limiter is actually in effect
    on this path too — see SafetyPolicy's `db` param.

    Mirrors the watch loop's own error handling: a failure partway through is
    logged and reported, not raised, so one bad cycle (e.g. a transient Gmail
    API hiccup) doesn't surface as a 500 any more than it would stop the
    watch loop from continuing to its next sleep/cycle.
    """
    db = get_db()
    dry_run = os.environ.get("MAILMIND_DRY_RUN", "") == "1"
    fetch_max = int(os.environ.get("MAILMIND_FETCH_MAX", "50"))
    retention_days = int(os.environ.get("MAILMIND_RETENTION_DAYS", "90"))
    no_llm = False  # matches the default `run --watch` invocation (no --no-llm flag)

    safety_policy = SafetyPolicy(dry_run=dry_run, db=db)

    try:
        _run_all_accounts(
            db, dry_run=dry_run, fetch_max=fetch_max, no_llm=no_llm,
            safety_policy=safety_policy,
        )
        _maybe_retrain(db)
        _maybe_prune(db, retention_days)
        _maybe_refresh_labels(db)
        _maybe_mirror_mailbox(db)
        _maybe_detect_loops(db)
        _maybe_unsnooze(db)
        _maybe_send_scheduled_drafts(db, dry_run=dry_run)
        _maybe_run_loop_radar(db, dry_run=dry_run, no_llm=no_llm)
        _maybe_propose_calendar_holds(db, dry_run=dry_run)
        _maybe_suggest_labels(db, no_llm=no_llm)
        _record_heartbeat(db)
    except Exception as exc:
        LOG.error("External poll cycle failed: %s", exc, exc_info=True)
        return {"status": "error", "detail": str(exc)}

    return {"status": "ok"}
