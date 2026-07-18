"""MailMind Dashboard — INBOX tab (Phase 2A: browse-all-mail).

Read-only browse surface over `get_all_emails()` — every email MailMind has
mirrored locally, most recent first, independent of queue/prediction status.
Mirrors the visual style and pagination pattern of the other tabs in
`mailmind/dashboard/app.py` (NOW/REVIEW/HISTORY/INSIGHTS/AUTOMATE).

Import-boundary note: this module intentionally does NOT import anything from
`mailmind.dashboard.app`. `app.py`'s `_TABS` registry will import
`render_inbox_tab` from here, so importing back from `app` would be a circular
import the moment that wiring lands (app -> tab_inbox -> app). Instead, the
small pieces this tab needs from app.py — `get_db()`'s cache_resource pattern
and the `_paginate`/`_load_more` pagination helpers — are duplicated locally,
function-for-function, mirroring app.py's current implementation exactly so
behavior stays identical.
"""
from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Optional

import streamlit as st

from mailmind.dashboard.helpers import (
    email_card_html,
    email_preview_html,
    get_time_ago_str,
    sender_avatar_html,
)
from mailmind.storage.database import Database
from mailmind.storage.queries import get_all_emails, get_thread_emails


# ---------------------------------------------------------------------------
# DB accessor — duplicated from app.py's get_db() (see module docstring for
# why this isn't imported instead).
# ---------------------------------------------------------------------------

@st.cache_resource
def get_db() -> Database:
    db_path = Path(os.environ.get("MAILMIND_DB_PATH", "~/.mailmind/mailmind.db")).expanduser()
    return Database(db_path)


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
    for idx, item in enumerate(page):
        gmail_id = item.get("gmail_id")
        thread_id = item.get("thread_id")
        subject = (item.get("subject") or "[No Subject]")[:80]
        sender = item.get("sender") or "Unknown"
        label = item.get("primary_label")
        channel = item.get("channel")
        conf = item.get("confidence")
        time_ago = get_time_ago_str(item.get("date_ts"))

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

        st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)

    _load_more(emails, "inbox")
