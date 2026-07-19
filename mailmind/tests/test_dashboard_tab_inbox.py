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


def _render_inbox_with_account(account=None):
    """Parametrizable wrapper for AppTest — pass account via kwargs= so this
    stays a plain, self-contained function (AppTest.from_function serialises
    only the function it's given, not the module it's defined in)."""
    from mailmind.dashboard import tab_inbox as _a  # noqa: PLC0415
    _a.render_inbox_tab(account=account)


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


def _db_row(**overrides) -> dict:
    """A plain dict standing in for a sqlite3.Row from db.get_email_by_gmail_id —
    dicts support the same `row['col']` access _resolve_email_for_action uses."""
    base = {
        "gmail_id": "msg_1",
        "thread_id": "thread_1",
        "sender": "Alice <alice@example.com>",
        "recipients": "bob@example.com",
        "subject": "Test subject",
        "snippet": "Test snippet body",
        "body_text": "Full body text",
        "date_ts": 1700000000,
        "labels": "INBOX",
        "parsed": 1,
    }
    base.update(overrides)
    return base


def _inbox_stack(emails, thread_emails=None, db=None, action_executor=None,
                  gmail_labels=None):
    stack = contextlib.ExitStack()
    mock_db = db if db is not None else MagicMock()
    stack.enter_context(patch("mailmind.dashboard.tab_inbox.get_db", return_value=mock_db))
    stack.enter_context(patch("mailmind.dashboard.tab_inbox.get_all_emails", return_value=emails))
    stack.enter_context(
        patch("mailmind.dashboard.tab_inbox.get_thread_emails",
              return_value=thread_emails if thread_emails is not None else [])
    )
    stack.enter_context(
        patch("mailmind.dashboard.tab_inbox.get_action_executor",
              return_value=action_executor)
    )
    stack.enter_context(
        patch("mailmind.dashboard.tab_inbox.get_gmail_labels",
              return_value=gmail_labels if gmail_labels is not None
              else ["WORK", "FINANCE", "PERSONAL", "NEWSLETTER"])
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


class TestBulkActions:
    """Phase 2D: bulk label/archive actions on browsed (non-queued) mail.

    Unlike the queue-based Approve/Reject flows, these go straight through
    ActionExecutor.execute_action — so the tests here mock execute_action
    itself and assert on how it's called, rather than on queue-row state."""

    def _select(self, at, gmail_id: str) -> None:
        at.checkbox(key=f"inbox_sel_{gmail_id}").check().run()
        assert not at.exception

    def test_checkbox_rendered_per_email_keyed_by_gmail_id(self):
        emails = [
            _email(gmail_id="msg_1", thread_id=None),
            _email(gmail_id="msg_2", thread_id=None),
        ]
        with _inbox_stack(emails):
            at = AppTest.from_function(_render_inbox)
            at.run()
        assert not at.exception
        keys = {cb.key for cb in at.checkbox}
        assert {"inbox_sel_msg_1", "inbox_sel_msg_2"} <= keys

    def test_no_action_bar_when_nothing_selected(self):
        emails = [_email(gmail_id="msg_1", thread_id=None)]
        with _inbox_stack(emails):
            at = AppTest.from_function(_render_inbox)
            at.run()
        assert not at.exception
        assert not [s for s in at.selectbox if s.key == "inbox_bulk_label"]
        assert not [b for b in at.button if "selected" in b.label]

    def test_action_bar_appears_after_selecting_one_item(self):
        emails = [
            _email(gmail_id="msg_1", thread_id=None),
            _email(gmail_id="msg_2", thread_id=None),
        ]
        with _inbox_stack(emails):
            at = AppTest.from_function(_render_inbox)
            at.run()
            self._select(at, "msg_1")
        apply_btns = [b for b in at.button if "Apply label to 1 selected" in b.label]
        archive_btns = [b for b in at.button if "Archive 1 selected" in b.label]
        assert len(apply_btns) == 1
        assert len(archive_btns) == 1

    def test_apply_label_calls_execute_action_once_per_selected_item(self):
        """(a) Selecting items and clicking 'Apply label' calls execute_action
        once per selected item, with the chosen label and action='label'."""
        emails = [
            _email(gmail_id="msg_1", subject="One", thread_id=None),
            _email(gmail_id="msg_2", subject="Two", thread_id=None),
            _email(gmail_id="msg_3", subject="Three", thread_id=None),
        ]
        mock_executor = MagicMock()
        mock_executor.execute_action.return_value = True
        mock_db = MagicMock()
        mock_db.get_email_by_gmail_id.side_effect = lambda gid: _db_row(gmail_id=gid)

        with _inbox_stack(emails, db=mock_db, action_executor=mock_executor):
            at = AppTest.from_function(_render_inbox)
            at.run()
            self._select(at, "msg_1")
            self._select(at, "msg_2")

            at.selectbox(key="inbox_bulk_label").select("FINANCE").run()
            assert not at.exception

            apply_btns = [b for b in at.button if "Apply label" in b.label]
            assert len(apply_btns) == 1
            apply_btns[0].click().run()

        assert not at.exception
        assert mock_executor.execute_action.call_count == 2
        calls = mock_executor.execute_action.call_args_list
        called_ids = {c.args[0].gmail_id for c in calls}
        assert called_ids == {"msg_1", "msg_2"}
        for c in calls:
            assert c.args[1] == "label"
            assert c.args[2].primary_label == "FINANCE"
            assert c.args[2].total_score == 100
            assert c.kwargs.get("confidence") == 1.0

    def test_mixed_success_failure_surfaced(self):
        """(b) A mixed success/failure result (1 of 2 succeed) is surfaced to
        the user, not silently swallowed."""
        emails = [
            _email(gmail_id="msg_1", thread_id=None),
            _email(gmail_id="msg_2", thread_id=None),
        ]
        mock_executor = MagicMock()
        mock_executor.execute_action.side_effect = [True, False]
        mock_db = MagicMock()
        mock_db.get_email_by_gmail_id.side_effect = lambda gid: _db_row(gmail_id=gid)

        with _inbox_stack(emails, db=mock_db, action_executor=mock_executor):
            at = AppTest.from_function(_render_inbox)
            at.run()
            self._select(at, "msg_1")
            self._select(at, "msg_2")
            apply_btns = [b for b in at.button if "Apply label" in b.label]
            apply_btns[0].click().run()

        assert not at.exception
        assert mock_executor.execute_action.call_count == 2
        toasts = [t.value for t in at.toast]
        assert any("1 of 2" in t and "1 failed" in t for t in toasts), toasts

    def test_all_success_has_no_failed_wording(self):
        emails = [_email(gmail_id="msg_1", thread_id=None)]
        mock_executor = MagicMock()
        mock_executor.execute_action.return_value = True
        mock_db = MagicMock()
        mock_db.get_email_by_gmail_id.side_effect = lambda gid: _db_row(gmail_id=gid)

        with _inbox_stack(emails, db=mock_db, action_executor=mock_executor):
            at = AppTest.from_function(_render_inbox)
            at.run()
            self._select(at, "msg_1")
            apply_btns = [b for b in at.button if "Apply label" in b.label]
            apply_btns[0].click().run()

        assert not at.exception
        toasts = [t.value for t in at.toast]
        assert any("1 of 1" in t and "failed" not in t for t in toasts), toasts

    def test_archive_uses_row_primary_label_not_dropdown_selection(self):
        """Safety-motivated design decision: archive's ScoreResult.primary_label
        must be the EMAIL's own current classification (from its
        get_all_emails row), never the unrelated label-picker selection —
        otherwise a URGENT/FINANCE/PERSONAL email could slip past
        SafetyPolicy's never-auto-archive guard just because the dropdown
        happened to be set to some other, unrestricted label."""
        emails = [_email(gmail_id="msg_1", thread_id=None, primary_label="URGENT")]
        mock_executor = MagicMock()
        mock_executor.execute_action.return_value = True
        mock_db = MagicMock()
        mock_db.get_email_by_gmail_id.side_effect = lambda gid: _db_row(gmail_id=gid)

        with _inbox_stack(emails, db=mock_db, action_executor=mock_executor,
                          gmail_labels=["WORK", "NEWSLETTER"]):
            at = AppTest.from_function(_render_inbox)
            at.run()
            self._select(at, "msg_1")
            # Dropdown defaults to its first option ("WORK") — unrelated to
            # the email's real "URGENT" classification.
            archive_btns = [b for b in at.button if "Archive" in b.label]
            assert len(archive_btns) == 1
            archive_btns[0].click().run()

        assert not at.exception
        score_arg = mock_executor.execute_action.call_args.args[2]
        assert mock_executor.execute_action.call_args.args[1] == "archive"
        assert score_arg.primary_label == "URGENT"

    def test_missing_credentials_shows_error_and_skips_execution(self):
        emails = [_email(gmail_id="msg_1", thread_id=None)]
        mock_db = MagicMock()
        with _inbox_stack(emails, db=mock_db, action_executor=None):
            at = AppTest.from_function(_render_inbox)
            at.run()
            self._select(at, "msg_1")
            apply_btns = [b for b in at.button if "Apply label" in b.label]
            apply_btns[0].click().run()
        assert not at.exception
        assert any("No Gmail credentials" in e.value for e in at.error)
        mock_db.get_email_by_gmail_id.assert_not_called()

    def test_selection_cleared_after_bulk_action(self):
        emails = [_email(gmail_id="msg_1", thread_id=None)]
        mock_executor = MagicMock()
        mock_executor.execute_action.return_value = True
        mock_db = MagicMock()
        mock_db.get_email_by_gmail_id.side_effect = lambda gid: _db_row(gmail_id=gid)

        with _inbox_stack(emails, db=mock_db, action_executor=mock_executor):
            at = AppTest.from_function(_render_inbox)
            at.run()
            self._select(at, "msg_1")
            apply_btns = [b for b in at.button if "Apply label" in b.label]
            apply_btns[0].click().run()

        assert not at.exception
        # _run_bulk_action pops the checkbox's session_state entry before its
        # st.rerun(), so the widget comes back unchecked on the following
        # render — this is the real, checkable signal that "clear the
        # selection state" (step 6) actually happened.
        assert at.checkbox(key="inbox_sel_msg_1").value is False

    def test_get_action_executor_called_with_account_per_call(self):
        """(c) Regression guard for the Phase 0 bug: a bulk action run while
        mailbox A is active must resolve credentials for A, and one run while
        mailbox B is active must resolve credentials for B — never silently
        defaulting to a single mailbox regardless of which is selected."""
        emails = [_email(gmail_id="msg_1", thread_id=None)]
        mock_executor = MagicMock()
        mock_executor.execute_action.return_value = True
        mock_db = MagicMock()
        mock_db.get_email_by_gmail_id.side_effect = lambda gid: _db_row(gmail_id=gid)

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("mailmind.dashboard.tab_inbox.get_db", return_value=mock_db))
            stack.enter_context(patch("mailmind.dashboard.tab_inbox.get_all_emails", return_value=emails))
            stack.enter_context(patch("mailmind.dashboard.tab_inbox.get_thread_emails", return_value=[]))
            mock_get_executor = stack.enter_context(
                patch("mailmind.dashboard.tab_inbox.get_action_executor", return_value=mock_executor)
            )
            stack.enter_context(
                patch("mailmind.dashboard.tab_inbox.get_gmail_labels", return_value=["WORK"])
            )

            at_a = AppTest.from_function(_render_inbox_with_account, kwargs={"account": "account_a"})
            at_a.run()
            self._select(at_a, "msg_1")
            apply_btns_a = [b for b in at_a.button if "Apply label" in b.label]
            apply_btns_a[0].click().run()
            assert not at_a.exception

            at_b = AppTest.from_function(_render_inbox_with_account, kwargs={"account": "account_b"})
            at_b.run()
            self._select(at_b, "msg_1")
            apply_btns_b = [b for b in at_b.button if "Apply label" in b.label]
            apply_btns_b[0].click().run()
            assert not at_b.exception

        called_accounts = [c.args[0] for c in mock_get_executor.call_args_list]
        assert "account_a" in called_accounts
        assert "account_b" in called_accounts


class TestReplyComposeFlow:
    """Phase 3E: reply/compose with a deliberate three-step gate — Save Draft,
    Approve, and Send are three separate button clicks/reruns, never
    collapsible into fewer. The real enforcement lives server-side in
    feedback.handle_approve_and_send (it re-reads the draft's status fresh
    from the database); these tests assert the UI never wires a single click
    to more than one of the three steps, and never renders a Send control
    except when the draft's current status is already 'approved'.
    """

    def _reply_stack(self, emails, draft=None, draft_id=42,
                      db=None, action_executor=None, gmail_labels=None):
        stack = contextlib.ExitStack()
        mock_db = db if db is not None else MagicMock()
        stack.enter_context(patch("mailmind.dashboard.tab_inbox.get_db", return_value=mock_db))
        stack.enter_context(patch("mailmind.dashboard.tab_inbox.get_all_emails", return_value=emails))
        stack.enter_context(patch("mailmind.dashboard.tab_inbox.get_thread_emails", return_value=[]))
        stack.enter_context(
            patch("mailmind.dashboard.tab_inbox.get_action_executor", return_value=action_executor)
        )
        stack.enter_context(
            patch("mailmind.dashboard.tab_inbox.get_gmail_labels",
                  return_value=gmail_labels or ["WORK", "FINANCE"])
        )
        stack.enter_context(patch("mailmind.dashboard.tab_inbox.create_draft", return_value=draft_id))
        stack.enter_context(patch("mailmind.dashboard.tab_inbox.get_draft", return_value=draft))
        stack.enter_context(patch("mailmind.dashboard.tab_inbox.update_draft_status", return_value=True))
        return stack

    def _draft(self, status, **overrides):
        base = {
            "id": 42, "status": status, "kind": "reply",
            "to_addrs": "alice@example.com", "subject": "Re: Test subject",
            "body_text": "Sounds good.", "gmail_message_id": None,
        }
        base.update(overrides)
        return base

    def test_reply_expander_present(self):
        with self._reply_stack([_email()]):
            at = AppTest.from_function(_render_inbox)
            at.run()
        assert not at.exception
        labels = [e.label for e in at.expander]
        assert any("Reply" in lbl for lbl in labels)

    def test_no_draft_shows_save_draft_not_approve_or_send(self):
        with self._reply_stack([_email()], draft=None):
            at = AppTest.from_function(_render_inbox)
            at.run()
        assert not at.exception
        labels = [b.label for b in at.button]
        assert any("Save Draft" in l for l in labels)
        assert not any("Approve" in l for l in labels)
        assert not any(l == "📤 Send" for l in labels)

    def test_save_draft_calls_create_draft_with_correct_fields(self):
        with self._reply_stack([_email(gmail_id="msg_1", thread_id="thread_1")],
                                draft=None) as _, \
             patch("mailmind.dashboard.tab_inbox.create_draft", return_value=42) as mock_create:
            at = AppTest.from_function(_render_inbox)
            at.run()
            save_btns = [b for b in at.button if "Save Draft" in b.label]
            assert len(save_btns) == 1
            save_btns[0].click().run()
        assert not at.exception
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        assert kwargs["kind"] == "reply"
        assert kwargs["in_reply_to_gmail_id"] == "msg_1"
        assert kwargs["thread_id"] == "thread_1"
        assert kwargs["generated_by"] == "human"

    def test_pending_review_shows_approve_and_discard_not_send(self):
        draft = self._draft("pending_review")
        with self._reply_stack([_email()], draft=draft):
            at = AppTest.from_function(_render_inbox)
            at.session_state["inbox_draft_id_msg_1"] = 42
            at.run()
        assert not at.exception
        labels = [b.label for b in at.button]
        assert any("Approve" in l for l in labels)
        assert any("Discard" in l for l in labels)
        assert not any("Send" in l for l in labels)

    def test_approve_button_only_updates_status_never_sends(self):
        draft = self._draft("pending_review")
        with self._reply_stack([_email()], draft=draft) as _, \
             patch("mailmind.dashboard.tab_inbox.update_draft_status", return_value=True) as mock_update, \
             patch("mailmind.intelligence.feedback.handle_approve_and_send") as mock_send:
            at = AppTest.from_function(_render_inbox)
            at.session_state["inbox_draft_id_msg_1"] = 42
            at.run()
            approve_btns = [b for b in at.button if "Approve" in b.label]
            assert len(approve_btns) == 1
            approve_btns[0].click().run()
        assert not at.exception
        mock_update.assert_called_once_with(mock_update.call_args.args[0], 42, "approved")
        mock_send.assert_not_called()

    def test_approved_status_shows_send_not_approve(self):
        draft = self._draft("approved")
        with self._reply_stack([_email()], draft=draft):
            at = AppTest.from_function(_render_inbox)
            at.session_state["inbox_draft_id_msg_1"] = 42
            at.run()
        assert not at.exception
        labels = [b.label for b in at.button]
        assert any("Send" in l for l in labels)
        assert not any("Approve" in l for l in labels)

    def test_send_button_calls_handle_approve_and_send_with_the_draft_id(self):
        draft = self._draft("approved")
        mock_executor = MagicMock()
        with self._reply_stack([_email()], draft=draft, action_executor=mock_executor) as _, \
             patch("mailmind.intelligence.feedback.handle_approve_and_send", return_value=True) as mock_send:
            at = AppTest.from_function(_render_inbox)
            at.session_state["inbox_draft_id_msg_1"] = 42
            at.run()
            send_btns = [b for b in at.button if b.label == "📤 Send"]
            assert len(send_btns) == 1
            send_btns[0].click().run()
        assert not at.exception
        mock_send.assert_called_once()
        args = mock_send.call_args.args
        assert args[1] == 42
        assert args[2] is mock_executor

    def test_sent_status_never_shows_approve_or_send(self):
        draft = self._draft("sent", gmail_message_id="abc123")
        with self._reply_stack([_email()], draft=draft):
            at = AppTest.from_function(_render_inbox)
            at.session_state["inbox_draft_id_msg_1"] = 42
            at.run()
        assert not at.exception
        # A real draft-state button ("New reply") must be present, confirming
        # we actually reached the 'sent' branch and this isn't vacuously
        # passing because no draft_id was set.
        labels = [b.label for b in at.button]
        assert any("New reply" in l for l in labels)
        assert not any("Approve" in l for l in labels)
        assert not any(l == "📤 Send" for l in labels)

    def test_send_failed_retry_only_reapproves_never_sends_in_same_click(self):
        """Adversarial-review regression: a 'send_failed' draft is NOT
        'approved' — retrying it must never call handle_approve_and_send
        within the same click as re-approving, even though the content was
        approved once before. The retry button may only flip status back to
        'approved'; a genuinely separate later click (the ordinary 'approved'
        branch's Send button) is what actually sends."""
        draft = self._draft("send_failed")
        with self._reply_stack([_email()], draft=draft) as _, \
             patch("mailmind.dashboard.tab_inbox.update_draft_status", return_value=True) as mock_update, \
             patch("mailmind.intelligence.feedback.handle_approve_and_send") as mock_send:
            at = AppTest.from_function(_render_inbox)
            at.session_state["inbox_draft_id_msg_1"] = 42
            at.run()
            retry_btns = [b for b in at.button if "Re-approve" in b.label]
            assert len(retry_btns) == 1
            assert not any("Retry send" in b.label for b in at.button)
            retry_btns[0].click().run()
        assert not at.exception
        mock_update.assert_called_once_with(mock_update.call_args.args[0], 42, "approved")
        mock_send.assert_not_called()

    def test_pending_review_draft_can_never_reach_handle_approve_and_send(self):
        """Regression guard for the core safety design: clicking every
        available button on a 'pending_review' draft (Approve, Discard) must
        never, under any circumstance, result in a call to
        handle_approve_and_send — that function is reachable only once a
        prior, separate click has already flipped status to 'approved'."""
        draft = self._draft("pending_review")
        with self._reply_stack([_email()], draft=draft) as _, \
             patch("mailmind.intelligence.feedback.handle_approve_and_send") as mock_send:
            at = AppTest.from_function(_render_inbox)
            at.session_state["inbox_draft_id_msg_1"] = 42
            at.run()
            clicked_any = False
            for b in list(at.button):
                if "Approve" in b.label or "Discard" in b.label:
                    b.click().run()
                    clicked_any = True
        assert not at.exception
        assert clicked_any, "expected Approve/Discard buttons to actually be present"
        mock_send.assert_not_called()


def a_file():
    from mailmind.dashboard import tab_inbox
    return tab_inbox.__file__
