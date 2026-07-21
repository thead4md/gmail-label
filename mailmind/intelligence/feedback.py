from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

from ..storage.database import Database
from ..storage.models import Email
from ..storage.queries import (
    log_correction, update_sender_profile, set_sender_label_rule,
    set_thread_label_rule, get_draft, update_draft_status,
)
from ..compose.composer import build_reply_mime, build_new_message_mime

LOG = logging.getLogger(__name__)

# Tiny EN+HU stopword set for subject-keyword extraction. Not exhaustive — just
# enough to keep a derived sender rule pattern from keying on filler words.
_SUBJECT_STOPWORDS = frozenset({
    "the", "and", "for", "your", "you", "with", "from", "this", "that", "have",
    "about", "please", "regarding", "update", "info", "information",
    "egy", "hogy", "nem", "igen", "kell", "lesz", "volt", "amely", "ezt", "azt",
})
# Strip common reply/forward prefixes (EN + HU) before extracting keywords.
_SUBJECT_PREFIX_RE = re.compile(r"^\s*(re|fw|fwd|v[aá]lasz|tov[aá]bb[ií]t[aá]s)\s*:\s*", re.I)


def _subject_match_pattern(subject: Optional[str], max_keywords: int = 3) -> Optional[str]:
    """Derive a case-insensitive subject regex from an email's subject keywords.

    Returns a ``kw1|kw2|kw3`` alternation (each keyword regex-escaped) so a sender
    rule created from the dashboard is CONDITIONAL on the email's topic instead of
    a blanket catch-all — honouring the content-over-sender labelling preference.
    Returns None when the subject yields no usable keywords (then the caller may
    fall back to a catch-all, which is only appropriate for single-purpose senders).
    """
    if not subject:
        return None
    cleaned = _SUBJECT_PREFIX_RE.sub("", subject)
    seen: set = set()
    keywords: list = []
    for tok in re.findall(r"\w+", cleaned, re.UNICODE):
        low = tok.lower()
        if len(low) < 4 or low in _SUBJECT_STOPWORDS or low.isdigit():
            continue
        if low in seen:
            continue
        seen.add(low)
        keywords.append(low)
        if len(keywords) >= max_keywords:
            break
    if not keywords:
        return None
    return "|".join(re.escape(k) for k in keywords)


def handle_approve(
    db: Database,
    queue_id: int,
    executor: Optional[Any] = None,
) -> bool:
    """Approve a queue item — and when an executor is provided, run the action.

    Pillar 2A: before this, the dashboard's Approve button merely flipped the
    queue row's status, and nothing ever applied approved actions to Gmail.
    Now an optional executor is invoked to actually perform the action, and
    the row's status reflects what happened (executed | execute_failed |
    approved when no executor was passed = legacy back-compat).

    Returns True if the item was found, False if it no longer exists (e.g.
    already processed by another tab — caller should show a warning).
    """
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            "SELECT email_gmail_id, action, confidence, priority_score "
            "FROM action_queue WHERE id = ?",
            (queue_id,),
        )
        queue_row = cur.fetchone()
        if not queue_row:
            return False

        gmail_id = queue_row['email_gmail_id']
        action = queue_row['action']
        confidence = float(queue_row['confidence'] or 0.0)
        priority_score = int(queue_row['priority_score'] or 0)
        # Provisional status — finalised below when executor is provided.
        cur.execute(
            "UPDATE action_queue SET status = 'approved', reviewed_at = ? WHERE id = ?",
            (now, queue_id),
        )

    # Build the Email + ScoreResult the executor needs.
    if executor is not None:
        new_status = _execute_approved_action(
            db, executor, queue_id=queue_id, gmail_id=gmail_id, action=action,
            confidence=confidence, priority_score=priority_score,
        )
        with db.transaction() as cur:
            if new_status == 'executed':
                cur.execute(
                    "UPDATE action_queue SET status = 'executed', executed_at = ? WHERE id = ?",
                    (int(time.time()), queue_id),
                )
            elif new_status == 'execute_failed':
                cur.execute(
                    "UPDATE action_queue SET status = 'execute_failed' WHERE id = ?",
                    (queue_id,),
                )
            # else leave 'approved' for the legacy/no-executor path.

    sender_row = db.execute_sql(
        "SELECT sender FROM emails WHERE gmail_id = ?", (gmail_id,)
    ).fetchone()
    if sender_row and sender_row['sender']:
        update_sender_profile(db, sender_row['sender'], 'approved')

    return True


def _execute_approved_action(
    db: Database,
    executor: Any,
    *,
    queue_id: int,
    gmail_id: str,
    action: str,
    confidence: float,
    priority_score: int,
) -> str:
    """Run the executor for an approved queue item.

    Returns one of: 'executed' | 'execute_failed' | 'approved' (when the
    email or score data couldn't be reconstructed, so we keep the legacy
    'approved' status as an audit trail without ever calling Gmail).
    """
    from ..processing.scorer import ScoreResult  # local: avoid cycle at import

    email_row = db.get_email_by_gmail_id(gmail_id)
    if email_row is None:
        LOG.warning("Approved queue %s references missing email %s — not executing.",
                    queue_id, gmail_id)
        return 'approved'

    # Resurrect a minimal Email from the cached row.
    email = Email(
        gmail_id=email_row['gmail_id'],
        thread_id=email_row['thread_id'],
        sender=email_row['sender'],
        recipients=(email_row['recipients'] or '').split(',') if email_row['recipients'] else [],
        subject=email_row['subject'],
        snippet=email_row['snippet'],
        body_text=email_row['body_text'],
        date_ts=email_row['date_ts'],
        labels=(email_row['labels'] or '').split(',') if email_row['labels'] else [],
        parsed=bool(email_row['parsed']),
    )
    # Look up the most recent primary_label so the executor (and downstream
    # safety policy) knows the category — critical for the auto-archive guard.
    pred_row = db.execute_sql(
        "SELECT primary_label FROM predictions WHERE email_gmail_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (gmail_id,),
    ).fetchone()
    primary_label = pred_row['primary_label'] if pred_row else None

    # If the user corrected the label (e.g. changed it in NOW/REVIEW before
    # approving), THAT — not the model's original prediction — is what must be
    # written to Gmail. handle_correction only logs to user_corrections (so the
    # prediction row and tier-quality analytics stay honest), so resolve the
    # most recent non-null corrected_label here and let it win.
    corr_row = db.execute_sql(
        "SELECT corrected_label FROM user_corrections "
        "WHERE email_gmail_id = ? AND corrected_label IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 1",
        (gmail_id,),
    ).fetchone()
    if corr_row and corr_row['corrected_label']:
        primary_label = corr_row['corrected_label']

    # Executor reads .total_score and .primary_label off ScoreResult; map the
    # queue row's normalised confidence back to a 0-100 integer for that path.
    score_total = int(round(confidence * 100)) if confidence else priority_score
    try:
        score = ScoreResult(
            total_score=score_total,
            base_score=score_total,
            rule_contribution=0,
            direct_mention_bonus=0,
            recency_bonus=0,
            sender_trust=0,
            primary_label=primary_label,
        )
        ok = executor.execute_action(email, action, score)
    except Exception as exc:
        LOG.error("Executor raised on approval of queue %s: %s", queue_id, exc, exc_info=True)
        return 'execute_failed'
    return 'executed' if ok else 'execute_failed'


def handle_reject(db: Database, queue_id: int, corrected_action: Optional[str] = None) -> bool:
    """Mark a queue item as rejected and update sender profile.

    Returns True if the item was found and rejected, False if it no longer exists.
    """
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute("SELECT email_gmail_id, action FROM action_queue WHERE id = ?", (queue_id,))
        queue_row = cur.fetchone()
        if not queue_row:
            return False

        gmail_id = queue_row['email_gmail_id']
        old_action = queue_row['action']
        cur.execute(
            "UPDATE action_queue SET status = 'rejected', reviewed_at = ? WHERE id = ?",
            (now, queue_id),
        )

    sender_row = db.execute_sql(
        "SELECT sender FROM emails WHERE gmail_id = ?", (gmail_id,)
    ).fetchone()
    if sender_row and sender_row['sender']:
        update_sender_profile(db, sender_row['sender'], 'rejected')

    if corrected_action:
        log_correction(
            db,
            gmail_id,
            original_label=None,
            corrected_label=None,
            original_action=old_action,
            corrected_action=corrected_action,
            source='dashboard',
        )

    return True


def handle_correction(
    db: Database,
    queue_id: int,
    corrected_label: Optional[str] = None,
    corrected_action: Optional[str] = None,
    executor: Optional[Any] = None,
) -> bool:
    """Handle user label/action correction, persist it, and (optionally) reapply it.

    Before this fix, a correction only ever inserted an audit row into
    user_corrections — it never updated the stored prediction and never
    touched Gmail, so re-opening the same email in the dashboard resurfaced
    the exact same stale label every time, no matter how many times it was
    "corrected". Now, mirroring the sibling handle_approve/handle_label_email:
      (a) the most recent prediction row for this email has its primary_label
          overwritten with corrected_label (classifier_source set to
          'user_correction'), so the dashboard stops resurfacing the stale
          label; and
      (b) when an executor is provided, the corrected label is reapplied to
          Gmail via the same execute_action path used by _execute_approved_action.

    executor defaults to None so existing callers (dashboard/app.py) keep
    working unchanged until they're updated to pass one.

    Returns True if the item was found and correction logged, False if not found.
    """
    with db.transaction() as cur:
        cur.execute("SELECT email_gmail_id, action FROM action_queue WHERE id = ?", (queue_id,))
        queue_row = cur.fetchone()
        if not queue_row:
            return False

        gmail_id = queue_row['email_gmail_id']
        original_action = queue_row['action']

    pred_row = db.execute_sql(
        "SELECT primary_label FROM predictions WHERE email_gmail_id = ? ORDER BY created_at DESC LIMIT 1",
        (gmail_id,),
    ).fetchone()
    original_label = pred_row['primary_label'] if pred_row else None

    log_correction(
        db,
        gmail_id,
        original_label=original_label,
        corrected_label=corrected_label,
        original_action=original_action,
        corrected_action=corrected_action,
        source='dashboard_correction',
    )

    if corrected_label:
        # Overwrite the stored prediction so the dashboard (and any future
        # read of this email's prediction) sees the corrected label instead
        # of resurfacing the model's original, now-known-wrong one.
        with db.transaction() as cur:
            cur.execute(
                "UPDATE predictions SET primary_label = ?, classifier_source = 'user_correction' "
                "WHERE email_gmail_id = ?",
                (corrected_label, gmail_id),
            )

    if executor is not None and corrected_label:
        from ..processing.scorer import ScoreResult  # local: avoid cycle at import

        email_row = db.get_email_by_gmail_id(gmail_id)
        if email_row is None:
            LOG.warning("Corrected queue %s references missing email %s — not executing.",
                        queue_id, gmail_id)
            return True

        # Resurrect a minimal Email from the cached row, same pattern as
        # _execute_approved_action.
        email = Email(
            gmail_id=email_row['gmail_id'],
            thread_id=email_row['thread_id'],
            sender=email_row['sender'],
            recipients=(email_row['recipients'] or '').split(',') if email_row['recipients'] else [],
            subject=email_row['subject'],
            snippet=email_row['snippet'],
            body_text=email_row['body_text'],
            date_ts=email_row['date_ts'],
            labels=(email_row['labels'] or '').split(',') if email_row['labels'] else [],
            parsed=bool(email_row['parsed']),
        )
        score = ScoreResult(
            total_score=100,
            base_score=100,
            rule_contribution=0,
            direct_mention_bonus=0,
            recency_bonus=0,
            sender_trust=0,
            primary_label=corrected_label,
        )
        try:
            executor.execute_action(email, 'label', score, confidence=1.0)
        except Exception as exc:
            LOG.error("Executor raised applying correction %s to queue %s: %s",
                      corrected_label, queue_id, exc, exc_info=True)

    return True


def handle_know_sender(db: Database, sender_email: str) -> bool:
    """Mark a sender as trusted (you know them)."""
    from ..storage.queries import set_sender_trust_tier
    if not sender_email:
        return False
    set_sender_trust_tier(db, sender_email, "trusted")
    return True


def handle_mute_sender(db: Database, sender_email: str) -> bool:
    """Mute a sender: watchlist tier (their mail is downranked, not deleted)."""
    from ..storage.queries import set_sender_trust_tier
    if not sender_email:
        return False
    set_sender_trust_tier(db, sender_email, "watchlist")
    return True


def handle_block_sender(db: Database, sender_email: str) -> bool:
    """Block a sender: watchlist tier + reject all their pending queue items."""
    from ..storage.queries import set_sender_trust_tier
    if not sender_email:
        return False
    set_sender_trust_tier(db, sender_email, "watchlist")
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            """UPDATE action_queue SET status = 'rejected', reviewed_at = ?
               WHERE status = 'pending' AND email_gmail_id IN (
                   SELECT gmail_id FROM emails WHERE sender = ?)""",
            (now, sender_email),
        )
    return True


def handle_label_email(
    db: Database,
    queue_id: int,
    label: str,
    scope: str,
    executor: Optional[Any] = None,
    account: Optional[str] = None,
    match_pattern: Optional[str] = None,
) -> bool:
    """Create a label rule based on user feedback.

    scope: "email" (one-off correction), "thread" (rule for this thread),
           or "sender" (rule for this sender)
    When executor is provided, applies the label to Gmail via the same path
    as handle_approve.

    match_pattern: optional subject regex for the "sender" scope. When omitted, a
    pattern is derived from the email's subject keywords so the sender rule is
    CONDITIONAL (content-over-sender preference) rather than a blanket catch-all.
    Pass an explicit "" to force a catch-all (single-purpose senders only).

    Returns True if the item was found and label rule set, False if not found.
    """
    if scope not in ("email", "thread", "sender"):
        raise ValueError(f"invalid scope: {scope}")

    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            "SELECT email_gmail_id, action FROM action_queue WHERE id = ?",
            (queue_id,),
        )
        queue_row = cur.fetchone()
        if not queue_row:
            return False

        gmail_id = queue_row["email_gmail_id"]
        original_action = queue_row["action"]

        # Get email details for thread_id, sender, and subject
        cur.execute("SELECT thread_id, sender, subject FROM emails WHERE gmail_id = ?", (gmail_id,))
        email_row = cur.fetchone()
        if not email_row:
            return False

        thread_id = email_row["thread_id"]
        sender = email_row["sender"]
        subject = email_row["subject"]

        # Create the rule based on scope
        if scope == "thread" and thread_id:
            set_thread_label_rule(db, thread_id, label)
            LOG.info(f"Created thread rule: thread_id={thread_id} → {label}")
        elif scope == "sender" and sender:
            # Conditional-by-default: scope the rule to the email's topic so it
            # doesn't relabel everything from this sender (content-over-sender).
            pattern = match_pattern if match_pattern is not None else _subject_match_pattern(subject)
            set_sender_label_rule(db, sender, label, account=account, match_pattern=pattern)
            LOG.info("Created sender rule: sender=%s → %s (subject pattern=%s)",
                     sender, label, pattern or "(catch-all)")
        # scope == "email" doesn't create a persistent rule, just logs the correction

        # Log the correction for training
        log_correction(
            db,
            gmail_id,
            original_label=None,
            corrected_label=label,
            original_action=original_action,
            corrected_action=None,
            source='dashboard_label_rule',
        )

    # Apply the label to Gmail if executor is provided
    if executor is not None:
        from ..processing.scorer import ScoreResult
        email_row = db.get_email_by_gmail_id(gmail_id)
        if email_row is None:
            LOG.warning("Labeled queue %s references missing email %s", queue_id, gmail_id)
            return True

        email = Email(
            gmail_id=email_row['gmail_id'],
            thread_id=email_row['thread_id'],
            sender=email_row['sender'],
            recipients=(email_row['recipients'] or '').split(',') if email_row['recipients'] else [],
            subject=email_row['subject'],
            snippet=email_row['snippet'],
            body_text=email_row['body_text'],
            date_ts=email_row['date_ts'],
            labels=(email_row['labels'] or '').split(',') if email_row['labels'] else [],
            parsed=bool(email_row['parsed']),
        )

        # Fake a high-confidence score for the executor
        score = ScoreResult(
            total_score=100,
            base_score=100,
            rule_contribution=0,
            direct_mention_bonus=0,
            recency_bonus=0,
            sender_trust=0,
            primary_label=label,
        )
        try:
            executor.execute_action(email, "label", score)
        except Exception as exc:
            LOG.error("Executor raised applying label %s to queue %s: %s", label, queue_id, exc, exc_info=True)

    return True


def handle_approve_and_send(db: Database, draft_id: int, executor: Any) -> bool:
    """Send an already-approved draft — the ONLY function permitted to call
    ``executor.send_message()``.

    This is the two-step send gate's enforcement point: a draft can only be sent if
    its status is *already* 'approved' in the database, and that transition must have
    happened via a SEPARATE, PRIOR call to ``update_draft_status(db, draft_id,
    'approved')`` — a distinct, earlier user interaction (e.g. a previous Streamlit
    rerun from an earlier "Approve" button click). This function never performs that
    transition itself; it only reads the draft's current status fresh from the
    database and refuses to proceed unless it is already 'approved'. Collapsing
    approve+send into one click here would defeat the entire point of the gate.

    Returns:
        True if the draft was already sent (or dry-run "sent"), or if the send
        attempt was correctly processed to a terminal status (including a failed
        send, which is recorded as 'send_failed' and reported as True here — the
        *approval step itself* was found valid and processed; the caller should
        check the draft's status/gmail_message_id if it needs the send outcome).
        False if the draft doesn't exist, isn't in 'approved' status, or an
        unexpected exception occurred (never propagated to the caller).
    """
    try:
        draft = get_draft(db, draft_id)
        if draft is None:
            LOG.warning("handle_approve_and_send: draft %s not found", draft_id)
            return False

        # Enforcement point: only a draft ALREADY in 'approved' status (set by a
        # prior, separate call) may be sent. No parameter or code path here can
        # perform that transition itself.
        if draft.get("status") != "approved":
            LOG.warning(
                "handle_approve_and_send: draft %s has status %r, not 'approved' — refusing to send",
                draft_id, draft.get("status"),
            )
            return False

        # Build the MIME message from the draft's content.
        try:
            if draft.get("kind") == "reply":
                original = None
                in_reply_to_gmail_id = draft.get("in_reply_to_gmail_id")
                if in_reply_to_gmail_id:
                    original = db.get_email_by_gmail_id(in_reply_to_gmail_id)
                raw_mime_b64url = build_reply_mime(
                    to_addr=draft.get("to_addrs") or "",
                    subject=draft.get("subject") or "",
                    body_text=draft.get("body_text") or "",
                    in_reply_to_message_id=(original["message_id"] if original else None),
                    references=(original["references_header"] if original else None),
                    cc_addr=draft.get("cc_addrs"),
                )
            else:
                raw_mime_b64url = build_new_message_mime(
                    to_addr=draft.get("to_addrs") or "",
                    subject=draft.get("subject") or "",
                    body_text=draft.get("body_text") or "",
                    cc_addr=draft.get("cc_addrs"),
                )
        except Exception as exc:
            LOG.error("handle_approve_and_send: failed to build MIME for draft %s: %s",
                      draft_id, exc, exc_info=True)
            update_draft_status(db, draft_id, "send_failed")
            return False

        result = executor.send_message(draft, raw_mime_b64url)

        if result:
            # A real send marks the draft 'sent' with the returned message id; a
            # dry-run ("dry_run" sentinel) means nothing was actually sent, so the
            # draft stays 'approved' rather than falsely claiming it was sent.
            if result == "dry_run":
                update_draft_status(db, draft_id, "approved")
            else:
                update_draft_status(
                    db, draft_id, "sent",
                    gmail_message_id=result,
                    sent_at=int(time.time()),
                )
            # If this draft is a Loop Radar nudge (loops.draft_id references
            # it), record that the nudge went out. Dry-run counts too --
            # dry-run only suppresses the literal Gmail write, not the rest of
            # this system's bookkeeping, matching how QueueManager/executor
            # already behave. A no-op for the vast majority of drafts, which
            # have no loop attached at all.
            try:
                from ..storage.queries import mark_loop_nudged_from_draft
                mark_loop_nudged_from_draft(db, draft_id)
            except Exception:
                LOG.debug("handle_approve_and_send: loop nudge bookkeeping failed (non-fatal)", exc_info=True)
            return True

        # send_message returned None: refused (rate-limited) or the Gmail API call
        # failed. Either way, mark it visibly so it can be retried.
        update_draft_status(db, draft_id, "send_failed")
        LOG.warning("handle_approve_and_send: send_message returned None for draft %s", draft_id)
        return False

    except Exception as exc:
        LOG.error("handle_approve_and_send: unexpected error for draft %s: %s",
                  draft_id, exc, exc_info=True)
        return False
