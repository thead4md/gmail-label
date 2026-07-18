"""Smoke tests for the INBOX tab (Phase 2A: browse-all-mail).

Follows the exact AppTest pattern already established in
test_dashboard_app.py: `AppTest.from_function` serialises a wrapper's source
and runs it in-process, so module-namespace patches (via unittest.mock.patch)
applied before `.run()` are visible to the render function.

`get_all_emails` / `get_thread_emails` are being built concurrently in
mailmind/storage/queries.py by another workstream and may not exist there yet
at the moment this file runs. To keep this test file honest to the *real*
contract (not a hand-rolled substitute), we inject minimal stub attributes
onto the `mailmind.storage.queries` module in-memory (no disk write, so it
never collides with the other workstream's concurrent edits to that file)
only if the real functions aren't there yet. This makes `tab_inbox.py`'s
normal `from mailmind.storage.queries import get_all_emails, get_thread_emails`
import succeed either way. Once the real functions land, this is a no-op.
"""
from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import mailmind.storage.queries as _queries

if not hasattr(_queries, "get_all_emails"):
    def _stub_get_all_emails(db, account=None, folder=None, search=None,
                              limit=25, offset=0):
        return []
    _queries.get_all_emails = _stub_get_all_emails

if not hasattr(_queries, "get_thread_emails"):
    def _stub_get_thread_emails(db, thread_id, account=None):
        return []
    _queries.get_thread_emails = _stub_get_thread_emails


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


def _render_inbox():
    from mailmind.dashboard import tab_inbox as _a  # noqa: PLC0415
    _a.render_inbox_tab()


def _email(**overrides) -> dict:
    base = {
        "gmail_id": "msg_1",
        "thread_id": "thread_1",
        "sender": "Alice <alice@example.com>",
        "subject": "Test subject",
        "snippet": "Test snippet body",
        "date_ts": 1700000000,
        "primary_label": "WORK",
        "channel": "team",
        "confidence": 0.85,
    }
    base.update(overrides)
    return base


def _inbox_stack(emails, thread_emails=None):
    stack = contextlib.ExitStack()
    stack.enter_context(patch("mailmind.dashboard.tab_inbox.get_db", return_value=MagicMock()))
    stack.enter_context(patch("mailmind.dashboard.tab_inbox.get_all_emails", return_value=emails))
    stack.enter_context(
        patch("mailmind.dashboard.tab_inbox.get_thread_emails",
              return_value=thread_emails if thread_emails is not None else [])
    )
    return stack


class TestRenderInboxTab:

    def test_empty_state_renders(self):
        with _inbox_stack([]):
            at = AppTest.from_function(_render_inbox)
            at.run()
        assert not at.exception
        all_md = " ".join(el.value for el in at.markdown)
        assert "mm-empty" in all_md
        assert "No mail yet" in all_md

    def test_single_item_renders_card_no_exception(self):
        with _inbox_stack([_email()]):
            at = AppTest.from_function(_render_inbox)
            at.run()
        assert not at.exception
        all_md = " ".join(el.value for el in at.markdown)
        assert "Test subject" in all_md
        assert "mm-card" in all_md

    def test_thread_button_present_when_thread_id_set(self):
        with _inbox_stack([_email(thread_id="thread_1")]):
            at = AppTest.from_function(_render_inbox)
            at.run()
        assert not at.exception
        thread_btns = [b for b in at.button if "thread" in b.label.lower()]
        assert len(thread_btns) == 1

    def test_no_thread_button_when_no_thread_id(self):
        with _inbox_stack([_email(thread_id=None)]):
            at = AppTest.from_function(_render_inbox)
            at.run()
        assert not at.exception
        thread_btns = [b for b in at.button if "thread" in b.label.lower()]
        assert len(thread_btns) == 0

    def test_clicking_thread_button_reveals_thread_messages(self):
        thread_msgs = [
            _email(gmail_id="msg_1", subject="First in thread"),
            _email(gmail_id="msg_2", subject="Reply in thread"),
        ]
        with _inbox_stack([_email(thread_id="thread_1")], thread_emails=thread_msgs):
            at = AppTest.from_function(_render_inbox)
            at.run()
            assert not at.exception
            thread_btns = [b for b in at.button if "thread" in b.label.lower()]
            assert len(thread_btns) == 1
            thread_btns[0].click().run()
        assert not at.exception
        all_md = " ".join(el.value for el in at.markdown)
        assert "Reply in thread" in all_md

    def test_many_items_shows_load_more_button(self):
        emails = [_email(gmail_id=f"msg_{i}", thread_id=None) for i in range(30)]
        with _inbox_stack(emails):
            at = AppTest.from_function(_render_inbox)
            at.run()
        assert not at.exception
        load_more_btns = [b for b in at.button if "Load more" in b.label]
        assert len(load_more_btns) == 1
        assert "5 remaining" in load_more_btns[0].label

    def test_render_inbox_tab_is_defined_and_is_fragment_callable(self):
        from mailmind.dashboard import tab_inbox as a
        assert callable(a.render_inbox_tab)

    def test_no_import_from_app_module(self):
        """Guard the documented no-circular-import design decision: tab_inbox.py
        must never import from mailmind.dashboard.app (mentions in the module
        docstring explaining *why* are fine — only actual import statements,
        checked via the AST so docstring prose can't cause a false positive,
        are disallowed)."""
        import ast
        import pathlib
        src = pathlib.Path(a_file()).read_text()
        tree = ast.parse(src)
        bad_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if "mailmind.dashboard.app" in node.module:
                    bad_modules.append(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "mailmind.dashboard.app" in alias.name:
                        bad_modules.append(alias.name)
        assert bad_modules == [], bad_modules


def a_file():
    from mailmind.dashboard import tab_inbox
    return tab_inbox.__file__
