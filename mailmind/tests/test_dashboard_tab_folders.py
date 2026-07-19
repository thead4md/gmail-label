"""Smoke tests for the FOLDERS tab (mailmind/dashboard/tab_folders.py).

Read-only UI tab per Phase 2C — a smoke test verifying render_folders_tab
runs without raising (for both the "has results" and "empty folder" cases)
is sufficient per this project's verification calibration (see the approved
plan's Phase 2 section).

get_gmail_labels/get_all_emails are mocked at their mailmind.storage.queries
call sites (create=True since get_all_emails is being built in parallel by
another agent and may not exist yet at test time).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest


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


def _render_folders():
    from mailmind.dashboard import tab_folders as _t  # noqa: PLC0415
    _t.render_folders_tab()


def _email(**overrides) -> dict:
    base = {
        "gmail_id": "msg_1",
        "thread_id": "thread_1",
        "sender": "alice@example.com",
        "subject": "Test subject",
        "snippet": "Test snippet",
        "date_ts": 1700000000,
        "primary_label": "WORK",
        "channel": "team",
        "confidence": 0.85,
    }
    base.update(overrides)
    return base


def test_folders_tab_renders_with_results():
    with patch("mailmind.dashboard.tab_folders.get_db", return_value=MagicMock()), \
         patch("mailmind.dashboard.tab_folders.get_gmail_labels",
               return_value=["WORK", "FINANCE"]), \
         patch("mailmind.dashboard.tab_folders.get_all_emails",
               return_value=[_email(), _email(gmail_id="msg_2", subject="Second")]):
        at = AppTest.from_function(_render_folders)
        at.run()
        assert not at.exception


def test_folders_tab_renders_with_zero_results():
    with patch("mailmind.dashboard.tab_folders.get_db", return_value=MagicMock()), \
         patch("mailmind.dashboard.tab_folders.get_gmail_labels",
               return_value=["WORK", "FINANCE"]), \
         patch("mailmind.dashboard.tab_folders.get_all_emails",
               return_value=[]):
        at = AppTest.from_function(_render_folders)
        at.run()
        assert not at.exception


def test_folders_tab_renders_with_no_labels_available():
    """Falls back to LABEL_COLORS keys when get_gmail_labels returns empty; even
    then it must not raise (LABEL_COLORS is always non-empty)."""
    with patch("mailmind.dashboard.tab_folders.get_db", return_value=MagicMock()), \
         patch("mailmind.dashboard.tab_folders.get_gmail_labels",
               return_value=[]), \
         patch("mailmind.dashboard.tab_folders.get_all_emails",
               return_value=[]):
        at = AppTest.from_function(_render_folders)
        at.run()
        assert not at.exception
