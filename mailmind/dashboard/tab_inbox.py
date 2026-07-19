"""MailMind Dashboard — INBOX tab (Phase 2A: browse-all-mail; Phase 2D: bulk
actions; Phase 3E: reply/compose).

Browse surface over `get_all_emails()` — every email MailMind has mirrored
locally, most recent first, independent of queue/prediction status. Mirrors
the visual style and pagination pattern of the other tabs in
`mailmind/dashboard/app.py` (NOW/REVIEW/HISTORY/INSIGHTS/AUTOMATE).

Phase 2D adds bulk label/archive actions: unlike REVIEW/NOW, most emails shown
here have no pending `action_queue` row, so the existing
handle_approve/handle_reject (queue-item based) don't apply. Instead this tab
builds a direct bulk-action flow straight through `ActionExecutor.execute_action`
— see `_resolve_email_for_action`, `_synthetic_score`, and `_run_bulk_action`.

Phase 3E adds a reply/compose flow with a deliberate THREE-STEP gate before
anything is ever sent: Save Draft -> Approve -> Send are three separate button
clicks (three separate Streamlit reruns), never collapsible into fewer. See
`_render_reply_flow`. The actual send-gate enforcement lives server-side in
`mailmind.intelligence.feedback.handle_approve_and_send` (it re-reads the
draft's status fresh from the database and refuses unless it is already
'approved') — this file's job is only to present the three steps as genuinely
separate UI actions, never to bypass or duplicate that enforcement.

Import-boundary note: this module intentionally does NOT import anything from
`mailmind.dashboard.app`. `app.py`'s `_TABS` registry will import
`render_inbox_tab` from here, so importing back from `app` would be a circular
import the moment that wiring lands (app -> tab_inbox -> app). Instead, the
small pieces this tab needs from app.py — `get_db()`'s cache_resource pattern,
`get_action_executor()`'s per-account executor cache, and the
`_paginate`/`_load_more` pagination helpers — are duplicated locally,
function-for-function, mirroring app.py's current implementation exactly so
behavior stays identical.
"""
from __future__ import annotations

import html
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

from mailmind.dashboard.helpers import (
    email_card_html,
    email_preview_html,
    get_time_ago_str,
    sender_avatar_html,
)
from mailmind.dashboard.theme import LABEL_COLORS
from mailmind.processing.scorer import ScoreResult
from mailmind.storage.database import Database
from mailmind.storage.models import Email
from mailmind.storage.queries import (
    create_draft,
    get_all_emails,
    get_draft,
    get_gmail_labels,
    get_thread_emails,
    update_draft_status,
)


# ---------------------------------------------------------------------------
# DB accessor — duplicated from app.py's get_db() (see module docstring for
# why this isn't imported instead).
# ---------------------------------------------------------------------------

@st.cache_resource
def get_db() -> Database:
    db_path = Path(os.environ.get("MAILMIND_DB_PATH", "~/.mailmind/mailmind.db")).expanduser()
    return Database(db_path)


@st.cache_resource
def get_action_executor(account: Optional[str]):
    """Build a real ActionExecutor for *account*, or None if it has no stored
    credentials.

    Duplicated from app.py's get_action_executor (see module docstring for why
    this file can't import from app.py) — kept byte-for-byte faithful,
    including per-account cache keying via st.cache_resource's argument-based
    keying. Previously (Phase 0) a parameterless singleton always resolved the
    PRIMARY mailbox's token, so every action for a secondary mailbox silently
    ran against the wrong Gmail service and 404'd; passing `account` through
    to `load_stored_credentials` here is what avoids regressing that.
    """
    from mailmind.actions.executor import ActionExecutor
    from mailmind.actions.safety import SafetyPolicy
    from mailmind.ingestion.auth import build_gmail_service, load_stored_credentials

    creds = load_stored_credentials(account)
    if creds is None:
        return None
    service = build_gmail_service(creds)
    dry_run = os.environ.get("MAILMIND_DRY_RUN", "0") == "1"
    return ActionExecutor(service=service, db=get_db(), safety_policy=SafetyPolicy(dry_run=dry_run))


@st.cache_resource
def get_llm_client() -> Optional[Any]:
    """Build a DeepSeek LLM client for AI-drafted replies, or None if unconfigured.

    Mirrors app.py's `_c_daily_brief` construction pattern exactly (same
    config gate, same client class, same best-effort try/except) — duplicated
    locally for the same circular-import reason as get_db/get_action_executor
    above.
    """
    from mailmind.config import MailMindConfig
    from mailmind.llm.deepseek import DeepSeekClient

    config = MailMindConfig.from_env()
    if not (config.llm_enabled and config.deepseek_api_key):
        return None
    try:
        return DeepSeekClient(config)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pagination — duplicated from app.py's _paginate/_load_more (see module
# docstring). Behavior must stay identical to the originals.
# ---------------------------------------------------------------------------

_PAGE_SIZE = 25


def _paginate(items: list, key: str, page_size: int = _PAGE_SIZE,
              reset_token=None) -> list:
    """Return the leading slice of `items` to render this run. Call `_load_more`
    AFTER the render loop to draw the 'Load more' button. Each caller lives in an
    @st.fragment, so the button rerun is local. `key` must be unique per list.

    `reset_token` identifies the underlying list. When it changes, the shown
    count resets to one page so we don't dump every row that the old, higher
    count would have revealed."""
    shown_key = f"_page_shown_{key}"
    token_key = f"_page_token_{key}"
    if st.session_state.get(token_key) != reset_token:
        st.session_state[token_key] = reset_token
        st.session_state[shown_key] = page_size
    shown = st.session_state.get(shown_key, page_size)
    return items[:shown]


def _load_more(items: list, key: str, page_size: int = _PAGE_SIZE) -> None:
    """Render a 'Load more' button when `items` has more than the shown slice."""
    shown_key = f"_page_shown_{key}"
    shown = st.session_state.get(shown_key, page_size)
    total = len(items)
    if shown >= total:
        return
    if st.button(f"Load more ({total - shown} remaining)", key=f"_loadmore_{key}",
                 use_container_width=True):
        st.session_state[shown_key] = shown + page_size
        st.rerun(scope="fragment")


# ---------------------------------------------------------------------------
# Bulk actions — direct execute_action path (no action_queue row involved).
#
# The emails shown in this tab are arbitrary browsed mail, most of which were
# never queued/predicted at all, so the queue-item-based
# handle_approve/handle_reject helpers (mailmind.intelligence.feedback) don't
# apply here. Instead we resurrect a minimal Email straight from its DB row —
# mirroring feedback.py's _execute_approved_action pattern exactly — and drive
# mailmind.actions.executor.ActionExecutor.execute_action() directly with a
# synthetic, maximally-confident ScoreResult. execute_action already owns all
# dry_run/SafetyPolicy gating, so this file adds no safety logic of its own.
# ---------------------------------------------------------------------------


def _resolve_email_for_action(db: Database, gmail_id: str) -> Optional[Email]:
    """Resurrect a minimal Email object from its DB row for the executor.

    Mirrors mailmind.intelligence.feedback._execute_approved_action's
    reconstruction byte-for-byte (same fields, same comma-split convention for
    recipients/labels) so the executor and SafetyPolicy see exactly the same
    Email shape they always do — no bespoke row-to-Email mapping introduced
    here that could silently diverge from the approve/reject path.
    """
    email_row = db.get_email_by_gmail_id(gmail_id)
    if email_row is None:
        return None
    return Email(
        gmail_id=email_row["gmail_id"],
        thread_id=email_row["thread_id"],
        sender=email_row["sender"],
        recipients=(email_row["recipients"] or "").split(",") if email_row["recipients"] else [],
        subject=email_row["subject"],
        snippet=email_row["snippet"],
        body_text=email_row["body_text"],
        date_ts=email_row["date_ts"],
        labels=(email_row["labels"] or "").split(",") if email_row["labels"] else [],
        parsed=bool(email_row["parsed"]),
    )


def _synthetic_score(primary_label: Optional[str]) -> ScoreResult:
    """Build a maximally-confident synthetic ScoreResult for a manual bulk
    action. total_score=100 plus the confidence=1.0 passed at the
    execute_action call site clears every CONFIDENCE_THRESHOLDS gate (label
    0.65, archive 0.85) — this is a deliberate, direct user action, not a
    model prediction, so there's no lower confidence to reflect."""
    return ScoreResult(
        total_score=100,
        base_score=100,
        rule_contribution=0,
        direct_mention_bonus=0,
        recency_bonus=0,
        sender_trust=0,
        primary_label=primary_label,
    )


def _run_bulk_action(
    db: Database,
    account: Optional[str],
    selected_ids: List[str],
    items_by_id: Dict[str, dict],
    action: str,
    chosen_label: Optional[str],
) -> None:
    """Apply `action` ('label' or 'archive') to every selected gmail_id.

    For 'label', the ScoreResult's primary_label is the label the user picked
    — that's the label being written to Gmail. For 'archive', it is
    deliberately the EMAIL'S OWN current primary_label (from the already-
    fetched row, not the label picker) — SafetyPolicy's never-auto-archive
    guard for URGENT/FINANCE/PERSONAL keys off score.primary_label, and
    archiving doesn't change the email's category, so gating it on the label
    picker's unrelated selection would let a sensitive email slip past that
    guard just because the dropdown happened to be set to something else.
    """
    executor = get_action_executor(account)
    if executor is None:
        st.error("No Gmail credentials found for this mailbox — cannot execute actions.")
        return

    success = 0
    failed = 0
    for gmail_id in selected_ids:
        email = _resolve_email_for_action(db, gmail_id)
        if email is None:
            failed += 1
            continue
        if action == "label":
            primary_label = chosen_label
        else:
            primary_label = (items_by_id.get(gmail_id) or {}).get("primary_label")
        score = _synthetic_score(primary_label)
        ok = executor.execute_action(email, action, score, confidence=1.0)
        if ok:
            success += 1
        else:
            failed += 1

    # Clear selection state for the items just processed, whether they
    # succeeded or not — retrying a failure re-selects it explicitly.
    for gmail_id in selected_ids:
        st.session_state.pop(f"inbox_sel_{gmail_id}", None)

    total = success + failed
    verb = "labeled" if action == "label" else "archived"
    if failed:
        st.toast(f"⚠️ {success} of {total} {verb}, {failed} failed", icon="⚠️")
    else:
        st.toast(f"✅ {success} of {total} {verb}", icon="✅")

    st.rerun()


# ---------------------------------------------------------------------------
# Reply / compose (Phase 3E) — a deliberate three-step gate: Save Draft,
# Approve, and Send are three separate buttons / separate reruns. The actual
# enforcement that a draft cannot be sent without having been separately
# approved lives server-side in feedback.handle_approve_and_send (it re-reads
# the draft's status fresh from the database) — this function only ever
# presents each step as its own distinct user action; it never has a single
# button that performs more than one of these three steps.
# ---------------------------------------------------------------------------


def _extract_reply_to_addr(sender: str) -> str:
    """Pull a bare email address out of a "Display Name <addr>" sender string."""
    sender = sender or ""
    if "<" in sender and ">" in sender:
        return sender.split("<", 1)[1].split(">", 1)[0].strip()
    return sender.strip()


def _render_reply_flow(db: Database, account: Optional[str], item: dict) -> None:
    gmail_id = item.get("gmail_id")
    if not gmail_id:
        return

    draft_id_key = f"inbox_draft_id_{gmail_id}"
    to_key = f"inbox_reply_to_{gmail_id}"
    subj_key = f"inbox_reply_subj_{gmail_id}"
    body_key = f"inbox_reply_body_{gmail_id}"

    with st.expander("↩️ Reply", expanded=False):
        draft_id = st.session_state.get(draft_id_key)
        draft = get_draft(db, draft_id) if draft_id else None

        if draft is None or draft.get("status") == "discarded":
            # --- Step 1: compose + Save Draft (no send, no approval) ---
            st.session_state.setdefault(to_key, _extract_reply_to_addr(item.get("sender") or ""))
            st.session_state.setdefault(subj_key, "Re: " + (item.get("subject") or ""))
            st.session_state.setdefault(body_key, "")

            st.text_input("To", key=to_key)
            st.text_input("Subject", key=subj_key)
            st.text_area("Message", key=body_key, height=160)

            col_ai, col_save = st.columns([1, 1])
            with col_ai:
                if st.button("✨ Draft with AI", key=f"inbox_ai_draft_{gmail_id}"):
                    from mailmind.intelligence.draft_reply import draft_reply

                    llm_client = get_llm_client()
                    if llm_client is None:
                        st.warning("AI drafting isn't configured for this deployment.")
                    else:
                        drafted = draft_reply(db, llm_client, item)
                        if drafted is None:
                            st.warning(
                                "Couldn't generate a draft right now (daily AI "
                                "draft budget reached, or the model call failed). "
                                "You can still write your own reply above."
                            )
                        else:
                            st.session_state[body_key] = drafted
                            st.rerun()
            with col_save:
                if st.button("💾 Save Draft", key=f"inbox_save_draft_{gmail_id}"):
                    new_id = create_draft(
                        db,
                        account=account,
                        kind="reply",
                        in_reply_to_gmail_id=gmail_id,
                        thread_id=item.get("thread_id"),
                        to_addrs=st.session_state.get(to_key, ""),
                        subject=st.session_state.get(subj_key, ""),
                        body_text=st.session_state.get(body_key, ""),
                        generated_by="human",
                    )
                    st.session_state[draft_id_key] = new_id
                    st.toast("Draft saved — review it below before approving.", icon="💾")
                    st.rerun()
            return

        # A draft exists — read its CURRENT status fresh from the DB (never
        # trust any cached/client-side notion of status) and render exactly
        # one next action, matching that status.
        status = draft.get("status")
        st.markdown(
            f'<span style="font-size:11px;padding:2px 8px;border-radius:10px;'
            f'background:#1C2237;color:#94A3B8;">{html.escape(status or "")}</span>',
            unsafe_allow_html=True,
        )
        st.markdown(f"**To:** {html.escape(draft.get('to_addrs') or '')}")
        st.markdown(f"**Subject:** {html.escape(draft.get('subject') or '')}")
        st.text_area(
            "Message", value=draft.get("body_text") or "",
            key=f"inbox_draft_preview_{gmail_id}", height=140, disabled=True,
        )

        if status == "pending_review":
            # --- Step 2: Approve (still no send) ---
            col_approve, col_discard = st.columns([1, 1])
            with col_approve:
                if st.button("✅ Approve", key=f"inbox_approve_draft_{gmail_id}"):
                    update_draft_status(db, draft_id, "approved")
                    st.toast("Draft approved — you can now send it.", icon="✅")
                    st.rerun()
            with col_discard:
                if st.button("🗑️ Discard", key=f"inbox_discard_draft_{gmail_id}"):
                    update_draft_status(db, draft_id, "discarded")
                    st.session_state.pop(draft_id_key, None)
                    st.rerun()

        elif status == "approved":
            # --- Step 3: Send. Only reachable because status is already
            # 'approved', which only Step 2 above (a separate, prior click)
            # can have set. ---
            col_send, col_discard = st.columns([1, 1])
            with col_send:
                if st.button("📤 Send", key=f"inbox_send_draft_{gmail_id}"):
                    from mailmind.intelligence.feedback import handle_approve_and_send

                    executor = get_action_executor(account)
                    if executor is None:
                        st.error("No Gmail credentials found for this mailbox — cannot send.")
                    else:
                        ok = handle_approve_and_send(db, draft_id, executor)
                        if ok:
                            st.toast("Sent.", icon="📤")
                        else:
                            st.error("Send failed — see the draft's status below to retry.")
                        st.rerun()
            with col_discard:
                if st.button("🗑️ Discard", key=f"inbox_discard_draft_{gmail_id}"):
                    update_draft_status(db, draft_id, "discarded")
                    st.session_state.pop(draft_id_key, None)
                    st.rerun()

        elif status == "sent":
            st.success(f"Sent (Gmail id: {draft.get('gmail_message_id') or 'dry-run'}).")
            if st.button("New reply", key=f"inbox_new_reply_{gmail_id}"):
                st.session_state.pop(draft_id_key, None)
                st.rerun()

        elif status == "send_failed":
            st.error("The last send attempt failed.")
            # Deliberately NOT a single "retry & send" button: status is
            # 'send_failed', not 'approved', right now, so sending in this
            # same click would be exactly the one-click approve+send
            # collapse the whole design exists to prevent — even though the
            # content was approved once before. This button only moves
            # status back to 'approved'; the ordinary 'approved' branch
            # above (a separate render, after this rerun) is what shows the
            # real "Send" button and performs the actual send.
            if st.button("↩️ Re-approve for retry", key=f"inbox_reapprove_retry_{gmail_id}"):
                update_draft_status(db, draft_id, "approved")
                st.rerun()


# ---------------------------------------------------------------------------
# Tab: INBOX
# ---------------------------------------------------------------------------


@st.fragment
def render_inbox_tab(account: Optional[str] = None) -> None:
    """Browse-all-mail tab: every locally-mirrored email, newest first.

    Unlike REVIEW/NOW (queue-status-scoped), this reads directly from
    `get_all_emails()` — it shows mail regardless of whether it was ever
    queued or has a prediction at all.
    """
    db = get_db()
    emails = get_all_emails(db, account=account, limit=200)

    if not emails:
        st.markdown(
            '<div class="mm-empty">'
            '<div class="mm-empty-icon">📪</div>'
            '<div class="mm-empty-text">No mail yet</div>'
            '<div class="mm-empty-sub">Nothing has been mirrored into the local '
            'database yet — run the pipeline or widen the ingestion window</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    st.caption(f"{len(emails)} email(s)")

    page = _paginate(emails, "inbox", reset_token=len(emails))
    items_by_id: Dict[str, dict] = {}
    selected_ids: List[str] = []
    for idx, item in enumerate(page):
        gmail_id = item.get("gmail_id")
        thread_id = item.get("thread_id")
        subject = (item.get("subject") or "[No Subject]")[:80]
        sender = item.get("sender") or "Unknown"
        label = item.get("primary_label")
        channel = item.get("channel")
        conf = item.get("confidence")
        time_ago = get_time_ago_str(item.get("date_ts"))

        if gmail_id:
            items_by_id[gmail_id] = item

        col_chk, col_card = st.columns([1, 24])
        with col_chk:
            checked = st.checkbox(
                "Select", key=f"inbox_sel_{gmail_id}", label_visibility="collapsed",
            )
            if checked and gmail_id:
                selected_ids.append(gmail_id)
        with col_card:
            st.markdown(
                email_card_html(
                    subject=subject,
                    sender=sender,
                    time_ago=time_ago,
                    label=label,
                    channel=channel,
                    confidence=conf,
                ),
                unsafe_allow_html=True,
            )

        snippet = item.get("snippet") or ""
        _prev = email_preview_html(snippet)
        if _prev:
            st.markdown(_prev, unsafe_allow_html=True)

        # Lightweight thread affordance: only calls get_thread_emails() once the
        # user actually clicks to expand, so a full page of rows costs zero
        # extra queries unless someone opens a thread.
        if thread_id:
            open_key = f"_inbox_thread_open_{gmail_id}"
            is_open = bool(st.session_state.get(open_key, False))
            btn_label = "🧵 Hide thread" if is_open else "🧵 View thread"
            if st.button(btn_label, key=f"inbox_thread_btn_{idx}_{gmail_id}"):
                st.session_state[open_key] = not is_open
                st.rerun()

            if st.session_state.get(open_key, False):
                thread_msgs = get_thread_emails(db, thread_id, account=account)
                with st.expander(f"🧵 Thread ({len(thread_msgs)} messages)", expanded=True):
                    for tmsg in thread_msgs:
                        t_subject = html.escape((tmsg.get("subject") or "[No Subject]")[:80])
                        t_sender_raw = tmsg.get("sender") or "Unknown"
                        t_sender = html.escape(t_sender_raw.split("<")[0].strip()[:40])
                        t_time = get_time_ago_str(tmsg.get("date_ts"))
                        st.markdown(
                            f'{sender_avatar_html(t_sender_raw)}&nbsp;'
                            f'<b>{t_sender}</b>&nbsp;{t_subject}&nbsp;'
                            f'<span style="font-size:11px;color:#94A3B8;">{t_time}</span>',
                            unsafe_allow_html=True,
                        )
                        t_prev = email_preview_html(tmsg.get("snippet") or "")
                        if t_prev:
                            st.markdown(t_prev, unsafe_allow_html=True)

        _render_reply_flow(db, account, item)

        st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)

    if selected_ids:
        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
        gmail_labels = get_gmail_labels(db, account=account) or list(LABEL_COLORS.keys())
        col_label, col_apply, col_archive = st.columns([2, 2, 2])
        with col_label:
            chosen_label = st.selectbox(
                "Bulk label", options=gmail_labels,
                key="inbox_bulk_label", label_visibility="collapsed",
            )
        with col_apply:
            if st.button(
                f"Apply label to {len(selected_ids)} selected",
                key="inbox_bulk_apply_btn", use_container_width=True,
            ):
                _run_bulk_action(
                    db, account, selected_ids, items_by_id,
                    action="label", chosen_label=chosen_label,
                )
        with col_archive:
            if st.button(
                f"Archive {len(selected_ids)} selected",
                key="inbox_bulk_archive_btn", use_container_width=True,
            ):
                _run_bulk_action(
                    db, account, selected_ids, items_by_id,
                    action="archive", chosen_label=chosen_label,
                )

    _load_more(emails, "inbox")
