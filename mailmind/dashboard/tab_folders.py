"""MailMind Dashboard — FOLDERS tab.

Folder/label navigation: pick a Gmail label from a sidebar-style list and
browse every local email tagged with it (substring match against the
`labels`/`user_labels` comma-separated columns, via
`mailmind.storage.queries.get_all_emails`).

This module is deliberately self-contained (own `get_db()`, own
`_paginate`/`_load_more`) rather than importing from `mailmind.dashboard.app`:
`app.py` is expected to import `render_folders_tab` from here to register it
in its `_TABS` list, so importing back from `app` would create a circular
import. The duplicated helpers are intentionally byte-for-byte identical in
behavior to their `app.py` counterparts.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import streamlit as st

from mailmind.dashboard.helpers import email_card_html, email_preview_html
from mailmind.dashboard.theme import LABEL_COLORS
from mailmind.storage.database import Database
from mailmind.storage.queries import get_all_emails, get_gmail_labels

# ---------------------------------------------------------------------------
# DB accessor (duplicated from app.py to avoid a circular import — app.py
# imports render_folders_tab from this module, so this module must not import
# from app.py).
# ---------------------------------------------------------------------------


@st.cache_resource
def get_db() -> Database:
    db_path = Path(os.environ.get("MAILMIND_DB_PATH", "~/.mailmind/mailmind.db")).expanduser()
    return Database(db_path)


# ---------------------------------------------------------------------------
# Cached read query
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def _c_gmail_labels(account):
    return get_gmail_labels(get_db(), account=account)


@st.cache_data(ttl=60)
def _c_folder_emails(account, folder, limit):
    return get_all_emails(get_db(), account=account, folder=folder, limit=limit)


# ---------------------------------------------------------------------------
# Pagination (duplicated from app.py's _paginate/_load_more — same behavior,
# see module docstring for why this isn't a shared import).
# ---------------------------------------------------------------------------

_PAGE_SIZE = 25


def _paginate(items: list, key: str, page_size: int = _PAGE_SIZE,
              reset_token=None) -> list:
    """Return the leading slice of `items` to render this run. Call `_load_more`
    AFTER the render loop to draw the 'Load more' button."""
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
# FOLDERS tab
# ---------------------------------------------------------------------------

def _section(icon: str, title: str) -> None:
    st.markdown(
        f'<div class="mm-section-header">{icon} {title}</div>',
        unsafe_allow_html=True,
    )


@st.fragment
def render_folders_tab(account: Optional[str] = None) -> None:
    """Folder/label navigation tab: pick a label, browse matching local mail."""
    _section("📁", "Folders")

    gmail_labels = _c_gmail_labels(account) or list(LABEL_COLORS.keys())
    if not gmail_labels:
        st.markdown(
            '<div class="mm-empty">'
            '<div class="mm-empty-icon">📁</div>'
            '<div class="mm-empty-text">No folders/labels available yet</div>'
            '<div class="mm-empty-sub">Labels appear here once Gmail label sync has run</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    selected_label = st.selectbox(
        "Folder", options=gmail_labels, index=0,
        key="folders_selected_label",
    )

    if not selected_label:
        st.info("Select a folder to browse its emails.")
        return

    items = _c_folder_emails(account, selected_label, 100) or []

    _section("📬", f"{selected_label} — {len(items)} email(s)")

    if not items:
        st.markdown(
            '<div class="mm-empty">'
            '<div class="mm-empty-icon">📭</div>'
            '<div class="mm-empty-text">No emails in this folder</div>'
            '<div class="mm-empty-sub">Nothing local matches this label yet</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    from mailmind.dashboard.helpers import get_time_ago_str

    for item in _paginate(items, f"folders_{selected_label}", reset_token=len(items)):
        subject = (item.get("subject") or "[No Subject]")[:80]
        sender = item.get("sender") or "Unknown"
        label = item.get("primary_label")
        channel = item.get("channel")
        conf = item.get("confidence") or 0.0
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

        st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)

    _load_more(items, f"folders_{selected_label}")
