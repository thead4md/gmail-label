"""MailMind — thread -> project conversion (client-strategy reframe §4.5).

Promotes a long multi-party thread into a durable "mini-project": the
union of participants and action items across every message in the
thread, plus (if a resolvable deadline was detected) a due date. Reuses
thread_analyzer's already-extracted action_items/deadlines (stored per
message on predictions.thread_context_json) rather than re-analyzing
anything -- this is pure aggregation.

Fully deterministic: no LLM, no network.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from .deadline_parser import parse_deadline_string
from .loops import split_addr

# Matches thread_analyzer._HU_REPLY_SUBJECT_RE's vocabulary, applied
# repeatedly so stacked prefixes ("Re: Re: Fwd: ...") are fully stripped.
_REPLY_PREFIX_RE = re.compile(r"^(re|fw|fwd|v[aá]lasz|tov[aá]bb[ií]t[aá]s):\s*", re.I | re.UNICODE)


def _clean_title(subject: Optional[str]) -> str:
    title = (subject or "").strip()
    while True:
        stripped = _REPLY_PREFIX_RE.sub("", title).strip()
        if stripped == title:
            break
        title = stripped
    return title or "(untitled thread)"


def promote_thread_to_project(db, thread_id: str, account: Optional[str] = None) -> int:
    """Aggregate every message in *thread_id* into a durable project row.

    Idempotent: promoting the same thread again (e.g. after new messages
    arrive) refreshes the same project (see queries.create_project's
    UNIQUE(account, thread_id)) rather than creating a duplicate.

    Raises ValueError if the thread has no locally-cached messages.
    """
    from ..storage.queries import create_project

    account_clause = " AND e.account = ?" if account else ""
    params: tuple = (thread_id, account) if account else (thread_id,)
    rows = db.execute_sql(
        f"""
        SELECT e.sender, e.recipients, e.subject, e.date_ts, p.thread_context_json
        FROM emails e
        LEFT JOIN predictions p ON p.email_gmail_id = e.gmail_id
        WHERE e.thread_id = ?{account_clause}
        ORDER BY e.date_ts ASC
        """,
        params,
    ).fetchall()
    if not rows:
        raise ValueError(f"No cached messages for thread {thread_id!r}")

    title = _clean_title(rows[0]["subject"])

    participants: Dict[str, Optional[str]] = {}
    for r in rows:
        addrs = [r["sender"]] + (r["recipients"] or "").split(",")
        for raw in addrs:
            email, name = split_addr(raw)
            if email and (email not in participants or not participants[email]):
                participants[email] = name

    action_items: List[str] = []
    deadlines: List[str] = []
    for r in rows:
        ctx_raw = r["thread_context_json"]
        if not ctx_raw:
            continue
        try:
            ctx = json.loads(ctx_raw)
        except (TypeError, ValueError):
            continue
        for it in ctx.get("action_items") or []:
            if it not in action_items:
                action_items.append(it)
        for d in ctx.get("deadlines") or []:
            if d not in deadlines:
                deadlines.append(d)

    now = int(time.time())
    deadline_ts: Optional[int] = None
    for d in deadlines:
        ts = parse_deadline_string(d, now)
        if ts is not None and (deadline_ts is None or ts < deadline_ts):
            deadline_ts = ts

    return create_project(
        db,
        account=account,
        thread_id=thread_id,
        title=title,
        participants=[{"email": e, "name": n} for e, n in participants.items()],
        action_items=action_items,
        deadline_ts=deadline_ts,
    )
