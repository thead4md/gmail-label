"""Smoke tests for the SEARCH tab (mailmind/dashboard/tab_search.py).

Follows the same AppTest.from_function pattern as test_dashboard_app.py:
each wrapper function imports the tab module fresh inside its body so
AppTest's temp-script execution has `st` in scope, and patches are applied
via unittest.mock.patch before AppTest.run().

`search_emails` (mailmind.storage.queries.search_emails) is being built in
parallel by another agent and may not exist yet at the time this test runs.
Per this codebase's convention (see test_dashboard_app.py, which patches
`mailmind.dashboard.app.get_pending_queue_enriched` rather than the
queries-module original), we patch the name at its point of use —
`mailmind.dashboard.tab_search.search_emails` — since tab_search imports it
via `from mailmind.storage.queries import search_emails`. This is read-only
UI per this project's verification calibration (Phase 2 plan), so a smoke
test is sufficient — no need for exhaustive coverage.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

# `search_emails` is being built in parallel (by another agent) in
# mailmind/storage/queries.py per the Phase 2B contract:
#   search_emails(db, query_text, account=None, limit=50, offset=0) -> list[dict]
# It may not exist yet at collection time. tab_search.py does
# `from mailmind.storage.queries import search_emails` at module scope, so
# without a stub that import (and therefore this whole test module) would
# fail with ImportError before any test runs. Install a harmless placeholder
# if it's missing so the module under test can import; every test below
# overrides it via `patch("mailmind.dashboard.tab_search.search_emails", ...)`
# so the placeholder's body never actually executes.
import mailmind.storage.queries as _queries  # noqa: E402

if not hasattr(_queries, "search_emails"):
    def _stub_search_emails(db, query_text, account=None, limit=50, offset=0):  # pragma: no cover
        return []
    _queries.search_emails = _stub_search_emails


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    try:
        st.cache_data.clear()
        st.cache_resource.clear()
    except Exception:
        pass
    yield
    try:
        st.cache_data.clear()
        st.cache_resource.clear()
    except Exception:
        pass


def _render_search():
    from mailmind.dashboard import tab_search as _a  # noqa: PLC0415
    _a.render_search_tab()


def _fake_results():
    now = int(time.time())
    return [
        {
            "gmail_id": "e1", "thread_id": "t1", "sender": "alice@example.com",
            "subject": "Project update", "snippet": "please review by Friday",
            "date_ts": now, "primary_label": "WORK", "channel": "team",
            "confidence": 0.9,
        },
        {
            "gmail_id": "e2", "thread_id": "t2", "sender": "bob@example.com",
            "subject": "Invoice #123", "snippet": "payment due",
            "date_ts": now - 3600, "primary_label": "FINANCE", "channel": "transactional",
            "confidence": 0.8,
        },
    ]


class TestSearchTabNoQuery:
    def test_no_query_shows_empty_state(self):
        with patch("mailmind.dashboard.tab_search.get_db", return_value=MagicMock()), \
             patch("mailmind.dashboard.tab_search.search_emails", return_value=[]) as mocked:
            at = AppTest.from_function(_render_search)
            at.run()
            assert not at.exception, f"tab raised: {at.exception}"
            blob = " ".join(el.value for el in at.markdown)
            assert "Type something to search" in blob
            # No query submitted yet -> search_emails must not be called.
            mocked.assert_not_called()


class TestSearchTabWithResults:
    def test_search_with_results_renders_cards(self):
        with patch("mailmind.dashboard.tab_search.get_db", return_value=MagicMock()), \
             patch("mailmind.dashboard.tab_search.search_emails", return_value=_fake_results()):
            at = AppTest.from_function(_render_search)
            at.run()
            assert not at.exception, f"tab raised: {at.exception}"

            # Simulate typing a query and submitting the form.
            at.text_input(key="search_query_input").set_value("project")
            at.button[0].click()  # form submit button
            at.run()

            assert not at.exception, f"tab raised after submit: {at.exception}"
            blob = " ".join(el.value for el in at.markdown)
            assert "Project update" in blob
            assert "DeltaGenerator" not in blob

    def test_search_with_zero_matches_shows_no_results(self):
        with patch("mailmind.dashboard.tab_search.get_db", return_value=MagicMock()), \
             patch("mailmind.dashboard.tab_search.search_emails", return_value=[]):
            at = AppTest.from_function(_render_search)
            at.run()

            at.text_input(key="search_query_input").set_value("nonexistent-xyz")
            at.button[0].click()
            at.run()

            assert not at.exception, f"tab raised: {at.exception}"
            blob = " ".join(el.value for el in at.markdown)
            assert "No results" in blob
