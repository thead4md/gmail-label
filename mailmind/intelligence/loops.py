"""MailMind — open-loop detection.

An *open loop* is an outstanding commitment on an email thread. This module
detects the ``waiting_on`` side: a thread whose newest message is outbound
(the user sent it) with no reply back — i.e. someone owes the user a response.
It is the durable, novel object behind the "Waiting on" lane of the reframed
NOW page and the seed for the autonomous follow-up ("Loop Radar") feature.

Design notes:
  - Fully deterministic. No LLM, no network. Reads the locally-mirrored
    INBOX + SENT mail that the watch loop already caches (see
    main._maybe_mirror_mailbox) so it costs nothing per run.
  - The ``you_owe`` side is deliberately NOT computed here — it is derived on
    read from the pending action_queue in the /api/now route.
  - Fold-in of the received-side "we'll get back to you"
    (thread_analyzer.waiting_on_other_party) signal is a planned enhancement;
    V1 keeps the crisp, high-precision "I sent, no reply" definition.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

LOG = logging.getLogger(__name__)

# How far back to scan mirrored mail when refreshing loops.
DEFAULT_LOOKBACK_DAYS = 14
# A waiting-on loop older than this (no reply) is flagged "about to slip".
DEFAULT_STALE_AFTER_DAYS = 2

_ADDR_RE = re.compile(r"<([^>]+)>")


def split_addr(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Split a ``"Name <email@host>"`` / ``email@host`` string into (email, name).

    A small shared utility -- reused by intelligence/draft_reply.py (voice
    exemplar lookup) and api/routers/now.py (VIP annotation), not just this
    module's own loop detection.
    """
    if not raw:
        return (None, None)
    raw = raw.strip()
    m = _ADDR_RE.search(raw)
    if m:
        email = m.group(1).strip().lower() or None
        name = raw[: m.start()].strip().strip('"').strip() or None
        return (email, name)
    return (raw.lower() or None, None)


def _labels_list(email: Dict[str, Any]) -> List[str]:
    raw = email.get("labels") or ""
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def _is_outbound(email: Dict[str, Any], user_addresses: Set[str]) -> bool:
    """A message is outbound if Gmail tagged it SENT or its sender is the user.

    Compares the *parsed* sender address for exact equality against
    ``user_addresses`` rather than testing substring containment — a raw
    ``addr in sender`` check would misclassify an unrelated inbound sender
    whose address happens to contain the user's address as a substring (e.g.
    user "me@x.com" vs. sender "awesome@x.com", which contains "me@x.com").
    """
    if "SENT" in _labels_list(email):
        return True
    sender_addr, _ = split_addr(email.get("sender"))
    return bool(sender_addr) and sender_addr in user_addresses


def _other_party(
    thread_msgs_sorted: List[Dict[str, Any]], user_addresses: Set[str]
) -> Tuple[Optional[str], Optional[str]]:
    """Best guess at the counterparty: the sender of the most recent inbound
    message, else the first recipient of the newest outbound message."""
    for e in reversed(thread_msgs_sorted):
        if not _is_outbound(e, user_addresses):
            return split_addr(e.get("sender"))
    for e in reversed(thread_msgs_sorted):
        recipients = e.get("recipients") or ""
        if recipients:
            return split_addr(recipients.split(",")[0])
    return (None, None)


def compute_thread_states(
    emails: List[Dict[str, Any]],
    user_addresses: Set[str],
    now_ts: int,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> Tuple[List[Dict[str, Any]], Set[str]]:
    """Pure core: from a list of email rows, return
    ``(open_loops, replied_thread_ids)``.

    ``open_loops`` are dicts ready for ``queries.upsert_loop`` (one per thread
    whose newest message is outbound). ``replied_thread_ids`` are threads whose
    newest message is inbound — their previously-open loops should be closed.
    Emails without a thread_id are ignored.
    """
    by_thread: Dict[str, List[Dict[str, Any]]] = {}
    for e in emails:
        tid = e.get("thread_id")
        if not tid:
            continue
        by_thread.setdefault(tid, []).append(e)

    open_loops: List[Dict[str, Any]] = []
    replied: Set[str] = set()
    for tid, msgs in by_thread.items():
        msgs_sorted = sorted(msgs, key=lambda m: (m.get("date_ts") or 0))
        newest = msgs_sorted[-1]
        if _is_outbound(newest, user_addresses):
            contact_email, contact_name = _other_party(msgs_sorted, user_addresses)
            last_sent = newest.get("date_ts") or now_ts
            open_loops.append(
                {
                    "thread_id": tid,
                    "contact_email": contact_email,
                    "contact_name": contact_name,
                    "subject": newest.get("subject"),
                    "last_sent_ts": last_sent,
                    "last_activity_ts": last_sent,
                    "due_ts": last_sent + stale_after_days * 86400,
                }
            )
        else:
            replied.add(tid)
    return open_loops, replied


def detect_waiting_on_loops(
    db,
    account: Optional[str] = None,
    user_addresses: Optional[Set[str]] = None,
    now_ts: Optional[int] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> Dict[str, int]:
    """Refresh waiting-on loops for one account from mirrored mail.

    Upserts a loop for every thread the user is waiting on, and closes any
    previously-open loop whose thread just received a reply (its newest message
    is now inbound). Threads that have simply aged out of the lookback window
    are left open — they are still unresolved. Returns ``{"open": n, "closed": m}``.
    """
    from mailmind.storage.queries import upsert_loop, get_open_loops, close_loop

    now = now_ts if now_ts is not None else int(time.time())
    since = now - lookback_days * 86400

    if user_addresses is None:
        user_addresses = {account.lower()} if account else set()
    else:
        user_addresses = {a.lower() for a in user_addresses if a}

    clauses = ["date_ts >= ?"]
    params: List[Any] = [since]
    if account:
        clauses.append("account = ?")
        params.append(account)
    rows = db.execute_sql(
        "SELECT gmail_id, thread_id, sender, recipients, subject, date_ts, labels, account"
        f" FROM emails WHERE {' AND '.join(clauses)}",
        tuple(params),
    ).fetchall()
    emails = [dict(r) for r in rows]

    open_loops, replied = compute_thread_states(
        emails, user_addresses, now, stale_after_days=stale_after_days
    )

    opened = 0
    for lp in open_loops:
        upsert_loop(db, account=account, side="waiting_on", **lp)
        opened += 1

    closed = 0
    for existing in get_open_loops(db, account=account, side="waiting_on", limit=1000):
        if existing.get("thread_id") in replied:
            if close_loop(db, existing["id"]):
                closed += 1

    return {"open": opened, "closed": closed}
