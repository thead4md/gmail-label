"""MailMind — Loop Radar: the autonomous follow-up closer.

For every stale "waiting_on" loop (see intelligence/loops.py), Loop Radar
drafts a context-aware nudge and either:

  - queues it for a human Approve + Send, exactly like the existing manual
    "Draft with AI" compose flow (the default for every contact), or
  - sends it autonomously, but ONLY for a contact the user has explicitly
    opted into auto-nudge via a separate, prior, one-time action (see
    queries.toggle_sender_auto_nudge) -- deliberately never the same flag as
    label/star/archive autopilot, since composing and sending new outbound
    content is a materially more consequential, harder-to-reverse action.

Autonomy never collapses compose+send into a single interaction: for an
eligible contact, the "prior separate human action" is the earlier opt-in
click on the Automate page, not anything this sweep does itself. The sweep
still performs two distinct, auditable steps (create the draft, then
transition it to 'approved') before ever calling
feedback.handle_approve_and_send -- the sole sanctioned sender of mail --
which independently re-checks the draft's status before sending, exactly as
it does for every other draft in this system.

After MAX_AUTO_NUDGES nudges with no reply, a loop is marked 'escalated' and
Radar stops touching it; the user needs to intervene by hand.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable, Optional

from ..storage.database import Database

LOG = logging.getLogger(__name__)

MAX_AUTO_NUDGES = 2
NUDGE_COOLDOWN_DAYS = 3  # minimum gap between successive nudges on one loop


def _day_start_ts() -> int:
    d = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp())


def draft_nudge(
    db: Database,
    llm_client: Any,
    loop: dict,
    daily_cost_cap_usd: float = 0.50,
) -> Optional[str]:
    """Generate a brief, polite follow-up nudge for a stale waiting-on loop,
    or None if unavailable.

    Mirrors intelligence.draft_reply.draft_reply's contract exactly (same
    daily $ cost cap checked via the same llm_usage ledger, same "never
    raises -- any failure returns None" behavior) but with a nudge-specific
    prompt: there may be no inbound message to answer here, this is a
    follow-up on the user's OWN prior outbound message.
    """
    if llm_client is None:
        return None

    from ..storage.queries import analytics_llm_cost, record_llm_usage
    from .draft_reply import _looks_hungarian

    try:
        spent_today = analytics_llm_cost(db, since_ts=_day_start_ts()).get("cost_usd", 0.0)
    except Exception:
        LOG.debug("draft_nudge: analytics_llm_cost failed, refusing to draft", exc_info=True)
        return None

    if spent_today >= daily_cost_cap_usd:
        LOG.info(
            "draft_nudge: daily cost cap reached ($%.4f >= $%.2f) — refusing to draft",
            spent_today, daily_cost_cap_usd,
        )
        return None

    subject = (loop.get("subject") or "")[:200]
    contact = loop.get("contact_name") or loop.get("contact_email") or "them"
    waiting_days = loop.get("waiting_days")
    if waiting_days is None:
        last = loop.get("last_activity_ts") or loop.get("last_sent_ts")
        waiting_days = int((int(time.time()) - last) / 86400) if last else 0

    hungarian = _looks_hungarian(subject, contact)
    language_instruction = (
        "Write the nudge in Hungarian." if hungarian else "Write the nudge in English."
    )

    system = (
        "You draft short, warm, professional follow-up nudges for a busy person "
        "checking in on a message they sent that has not been answered yet. "
        "Reply with ONLY the body text of the nudge — no subject line, no "
        "explanations about what you did. Keep it brief (2-4 sentences), assume "
        "good faith (the recipient is likely just busy), and make it trivially "
        "easy to respond to. " + language_instruction
    )
    from .draft_reply import _voice_examples_block

    user_parts = [
        f"I sent an email {waiting_days} day(s) ago to {contact} with the subject "
        f'"{subject}" and have not heard back. Draft a brief, friendly follow-up '
        "checking in.",
    ]
    voice_block = _voice_examples_block(db, loop.get("contact_email"), account=loop.get("account"))
    if voice_block:
        user_parts += ["", voice_block]
    user = "\n".join(user_parts)

    try:
        from ..llm.chat import chat_complete

        t0 = time.monotonic()
        content, resp, model = chat_complete(
            llm_client, system, user,
            temperature=0.4, max_tokens=200, return_usage=True,
        )
        elapsed_s = time.monotonic() - t0
    except Exception:
        LOG.warning("draft_nudge: LLM call failed", exc_info=True)
        return None

    try:
        from ..ml.llm_classifier import log_llm_usage, drain_pending_usage

        log_llm_usage(model, resp, elapsed_s, kind="loop_radar_nudge")
        record_llm_usage(db, drain_pending_usage())
    except Exception:
        LOG.debug("draft_nudge: usage recording failed (non-fatal)", exc_info=True)

    content = (content or "").strip()
    return content or None


def _needs_action(loop: dict, now_ts: int) -> bool:
    """Whether Loop Radar should look at this loop at all this sweep --
    either to draft a (re-)nudge or to escalate it. Excludes loops already
    awaiting human review (nudge_drafted), already escalated/closed/snoozed,
    or still on cooldown after a prior nudge.

    Deliberately uses ``is not None`` rather than truthiness for due_ts/
    last_nudge_ts: a falsy-but-set epoch timestamp of exactly 0 must still
    count as "set", not be treated the same as "never set".
    """
    state = loop.get("state") or "open"
    if state in ("nudge_drafted", "escalated", "closed", "snoozed"):
        return False
    if state == "open":
        due = loop.get("due_ts")
        return due is not None and due <= now_ts
    if state == "nudged":
        if (loop.get("nudge_count") or 0) >= MAX_AUTO_NUDGES:
            return True  # needs action: escalate, handled by the caller
        last_nudge = loop.get("last_nudge_ts")
        if last_nudge is not None and now_ts - last_nudge < NUDGE_COOLDOWN_DAYS * 86400:
            return False  # still cooling down since the last nudge
        return True  # needs action: draft a re-nudge
    return False


def run_loop_radar_sweep(
    db: Database,
    llm_client: Any,
    executor_for_account: Callable[[Optional[str]], Any],
    account: Optional[str] = None,
    now_ts: Optional[int] = None,
) -> dict:
    """Process every eligible open 'waiting_on' loop for one account.

    ``executor_for_account`` is a callback ``account -> ActionExecutor | None``
    so credential resolution stays the caller's responsibility (mirrors
    main._maybe_send_scheduled_drafts, which builds/caches one executor per
    account per cycle) -- this function never authenticates anything itself.

    Returns counts: {"drafted": queued for human review, "auto_sent": sent
    autonomously (earned per-contact opt-in), "escalated": nudge budget
    exhausted with no reply, "skipped": drafting failed or no contact email}.
    """
    from ..storage.queries import (
        get_open_loops, escalate_loop, link_loop_draft, create_draft,
        is_sender_auto_nudge_eligible, update_draft_status,
    )
    from .feedback import handle_approve_and_send

    now = now_ts if now_ts is not None else int(time.time())
    counts = {"drafted": 0, "auto_sent": 0, "escalated": 0, "skipped": 0}

    loops = get_open_loops(db, account=account, side="waiting_on", limit=200)
    for loop in loops:
        if not _needs_action(loop, now):
            continue

        if loop.get("state") == "nudged" and (loop.get("nudge_count") or 0) >= MAX_AUTO_NUDGES:
            escalate_loop(db, loop["id"])
            counts["escalated"] += 1
            continue

        contact_email = loop.get("contact_email")
        if not contact_email:
            counts["skipped"] += 1
            continue

        body = draft_nudge(db, llm_client, loop)
        if not body:
            counts["skipped"] += 1
            continue

        subject = loop.get("subject")
        draft_id = create_draft(
            db,
            account=loop.get("account"),
            kind="compose",
            to_addrs=contact_email,
            subject=f"Re: {subject}" if subject else "Following up",
            body_text=body,
            generated_by="llm",
        )

        eligible = is_sender_auto_nudge_eligible(db, contact_email)
        if not eligible:
            link_loop_draft(db, loop["id"], draft_id, state="nudge_drafted")
            counts["drafted"] += 1
            continue

        # Earned autonomy: contact was explicitly opted in beforehand (a
        # separate, prior action). Advance the draft through the SAME two
        # tracked steps a human would (approve, then send) -- never a single
        # collapsed call -- so handle_approve_and_send's own status check
        # still means what it always means.
        link_loop_draft(db, loop["id"], draft_id, state="nudge_drafted")
        try:
            update_draft_status(db, draft_id, "approved")
            executor = executor_for_account(loop.get("account"))
            if executor is None:
                counts["drafted"] += 1
                continue
            sent = handle_approve_and_send(db, draft_id, executor)
        except Exception:
            LOG.warning("run_loop_radar_sweep: auto-send failed for loop %s", loop["id"], exc_info=True)
            sent = False

        if sent:
            counts["auto_sent"] += 1
        else:
            counts["drafted"] += 1  # fell back to awaiting human review

    return counts
