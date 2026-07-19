"""MailMind Dashboard — SEARCH tab.

Phase 2B: a simple SQLite `LIKE`-backed search over subject/sender/snippet/
body_text (see mailmind.storage.queries.search_emails). Deliberately reuses
the existing dashboard visual language (email_card_html/email_preview_html)
and the app.py pagination idiom rather than inventing new UI patterns.

Import-safety note: this module does NOT import `mailmind.dashboard.app` at
module scope. app.py's `_TABS` registry imports every tab module (including
this one) at import time, so an `from mailmind.dashboard.app import ...` here
would be circular (app -> tab_search -> app, with app only partially
initialized). `get_db`/`_paginate`/`_load_more` are therefore small, faithful
local copies of app.py's implementations rather than shared imports.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import streamlit as st

from mailmind.dashboard.helpers import (
    email_card_html,
    email_preview_html,
    get_time_ago_str,
)
from mailmind.storage.database import Database
from mailmind.storage.queries import search_emails

# ---------------------------------------------------------------------------
# DB accessor — duplicated from app.py's @st.cache_resource get_db() (thin
# wrapper, safe to duplicate; see module docstring for why it isn't imported).
# ---------------------------------------------------------------------------


@st.cache_resource
def get_db() -> Database:
    db_path = Path(os.environ.get("MAILMIND_DB_PATH", "~/.mailmind/mailmind.db")).expanduser()
    return Database(db_path)


# ---------------------------------------------------------------------------
# Pagination — duplicated from app.py's _paginate/_load_more (identical
# behavior; see module docstring for why it isn't imported).
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
# Tab: SEARCH
# ---------------------------------------------------------------------------


@st.fragment
def render_search_tab(account: Optional[str] = None) -> None:
    """Render the SEARCH tab: a submit-on-Enter/button search box over
    subject/sender/snippet/body_text, results rendered with the same card
    style used elsewhere in the dashboard.
    """
    db = get_db()

    st.markdown(
        '<div class="mm-section-header">🔍 Search mail</div>',
        unsafe_allow_html=True,
    )

    # Wrapped in st.form so Streamlit's per-keystroke rerun doesn't fire a
    # DB query on every character typed — only on Enter / the Search button.
    with st.form(key="search_form", clear_on_submit=False):
        query_text = st.text_input(
            "Search mail",
            placeholder="Search subject, sender, or body…",
            key="search_query_input",
            label_visibility="collapsed",
        )
        submitted = st.form_submit_button("Search", use_container_width=False)

    if submitted:
        st.session_state["search_last_query"] = query_text

    active_query = (st.session_state.get("search_last_query") or "").strip()

    if not active_query:
        st.markdown(
            '<div class="mm-empty">'
            '<div class="mm-empty-icon">🔍</div>'
            '<div class="mm-empty-text">Type something to search</div>'
            '<div class="mm-empty-sub">Searches subject, sender, and body text</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    results = search_emails(db, active_query, account=account, limit=100)

    if not results:
        st.markdown(
            '<div class="mm-empty">'
            '<div class="mm-empty-icon">📭</div>'
            '<div class="mm-empty-text">No results</div>'
            f'<div class="mm-empty-sub">Nothing matched &ldquo;{st.session_state.get("search_last_query", "")}&rdquo;</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(f"**{len(results)} result(s)** for “{active_query}”")
    st.markdown("")

    for item in _paginate(results, "search", reset_token=(active_query, len(results))):
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

    _load_more(results, "search")
