"""Streamlit render-level tests for the MailMind dashboard.

Uses streamlit.testing.v1.AppTest (built into Streamlit ≥ 1.18, no extra package).

Root cause of AppTest.from_function isolation:
  `AppTest.from_function` serialises the function body via inspect.getsource()
  and runs it in a fresh temp script that does NOT inherit module-level imports.
  The fix: all wrapper functions (`_render_now`, `_render_review`) include their
  own `from mailmind.dashboard import app` import so that when AppTest runs the
  temp script, the import executes and render_now_tab / render_review_tab find
  `st` in their module globals.

Patches are applied via unittest.mock.patch before AppTest.run().  Because
AppTest runs in the same process, module-namespace patches are visible to the
render functions.
"""
from __future__ import annotations

import contextlib
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest


# ---------------------------------------------------------------------------
# AppTest-compatible wrappers
# Each function imports the app module inside its body so that AppTest's
# temp-script execution has the import (and therefore streamlit.st) in scope.
# ---------------------------------------------------------------------------

def _render_now():
    from mailmind.dashboard import app as _a  # noqa: PLC0415
    _a.render_now_tab()


def _render_review():
    from mailmind.dashboard import app as _a  # noqa: PLC0415
    _a.render_review_tab()


# ---------------------------------------------------------------------------
# Test-data factory
# ---------------------------------------------------------------------------

def _item(**overrides) -> dict:
    base = {
        'id': 1,
        'email_gmail_id': 'msg_test',
        'prediction_id': None,
        'action': 'star',
        'sender': 'alice@example.com',
        'subject': 'Test subject',
        'confidence': 0.85,
        'priority_score': 85,
        'status': 'pending',
        'reason_json': {
            'reply_needed': False,
            'thread_summary': None,
            'similar_past_actions': [],
            'trust_tier': 'neutral',
            'rule_matches': [],
            'score_breakdown': {},
            'ml_confidence': None,
            'llm_confidence': None,
            'primary_label': 'WORK',
            'score': 85,
        },
        'trust_tier': 'neutral',
        'display_name': None,
        'date_ts': 1700000000,
        'snippet': 'Test snippet',
        'total_approved': 0,
        'total_rejected': 0,
        'auto_action_eligible': False,
        'primary_label': 'WORK',
        'prediction_confidence': 0.85,
        'ml_confidence': None,
        'llm_confidence': None,
        'created_at': 1700000000,
        'updated_at': 1700000000,
        'reviewed_at': None,
        'executed_at': None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Patch-stack helpers
# ---------------------------------------------------------------------------

def _now_stack(items, filter_out=None, approve_ret=True):
    """Return an ExitStack that patches all NOW-tab dependencies."""
    stack = contextlib.ExitStack()
    stack.enter_context(patch('mailmind.dashboard.app.get_db', return_value=MagicMock()))
    stack.enter_context(
        patch('mailmind.dashboard.app.get_pending_queue_enriched', return_value=items)
    )
    stack.enter_context(
        patch(
            'mailmind.dashboard.app.filter_now_items',
            return_value=(filter_out if filter_out is not None else items),
        )
    )
    stack.enter_context(
        patch('mailmind.dashboard.app.handle_approve', return_value=approve_ret)
    )
    return stack


def _review_stack(items, approve_ret=True, reject_ret=True):
    """Return an ExitStack that patches all REVIEW-tab dependencies."""
    stack = contextlib.ExitStack()
    stack.enter_context(patch('mailmind.dashboard.app.get_db', return_value=MagicMock()))
    stack.enter_context(
        patch('mailmind.dashboard.app.get_pending_queue_enriched', return_value=items)
    )
    stack.enter_context(
        patch('mailmind.dashboard.app.handle_approve', return_value=approve_ret)
    )
    stack.enter_context(
        patch('mailmind.dashboard.app.handle_reject', return_value=reject_ret)
    )
    stack.enter_context(
        patch('mailmind.dashboard.app.handle_correction', return_value=True)
    )
    return stack


# ---------------------------------------------------------------------------
# NOW tab tests
# ---------------------------------------------------------------------------

class TestRenderNowTab:

    def test_empty_queue_shows_info_and_no_buttons(self):
        with _now_stack([], filter_out=[]):
            at = AppTest.from_function(_render_now)
            at.run()
        assert not at.exception
        assert len(at.info) >= 1
        assert len(at.button) == 0

    def test_single_item_shows_exactly_one_approve_button(self):
        item = _item(priority_score=90)
        item['reason_json']['reply_needed'] = True
        with _now_stack([item]):
            at = AppTest.from_function(_render_now)
            at.run()
        assert not at.exception
        approve_btns = [b for b in at.button if 'Approve' in b.label]
        assert len(approve_btns) == 1

    def test_no_reject_button_in_now_tab(self):
        item = _item(priority_score=90)
        item['reason_json']['reply_needed'] = True
        with _now_stack([item]):
            at = AppTest.from_function(_render_now)
            at.run()
        assert not at.exception
        reject_btns = [b for b in at.button if 'Reject' in b.label]
        assert len(reject_btns) == 0

    def test_no_edit_button_in_now_tab(self):
        item = _item(priority_score=90)
        item['reason_json']['reply_needed'] = True
        with _now_stack([item]):
            at = AppTest.from_function(_render_now)
            at.run()
        assert not at.exception
        edit_btns = [b for b in at.button if 'Edit' in b.label]
        assert len(edit_btns) == 0

    def test_race_condition_approve_false_shows_warning(self):
        item = _item(priority_score=90)
        item['reason_json']['reply_needed'] = True
        with _now_stack([item], approve_ret=False):
            at = AppTest.from_function(_render_now)
            at.run()
            assert not at.exception
            approve_btns = [b for b in at.button if 'Approve' in b.label]
            assert len(approve_btns) >= 1
            approve_btns[0].click()
            at.run()
        assert not at.exception
        assert len(at.warning) >= 1

    def test_reply_needed_badge_shown(self):
        item = _item(priority_score=90)
        item['reason_json']['reply_needed'] = True
        with _now_stack([item]):
            at = AppTest.from_function(_render_now)
            at.run()
        assert not at.exception
        all_md = ' '.join(el.value for el in at.markdown)
        assert 'Reply Needed' in all_md

    def test_thread_summary_shown_when_present(self):
        item = _item(priority_score=90)
        item['reason_json']['reply_needed'] = True
        item['reason_json']['thread_summary'] = 'Waiting for sign-off'
        with _now_stack([item]):
            at = AppTest.from_function(_render_now)
            at.run()
        assert not at.exception
        all_md = ' '.join(el.value for el in at.markdown)
        assert 'Waiting for sign-off' in all_md


# ---------------------------------------------------------------------------
# REVIEW tab tests
# ---------------------------------------------------------------------------

class TestRenderReviewTab:

    def test_empty_queue_shows_info(self):
        with _review_stack([]):
            at = AppTest.from_function(_render_review)
            at.run()
        assert not at.exception
        assert len(at.info) >= 1

    def test_single_item_shows_approve_reject_edit_buttons(self):
        item = _item()
        with _review_stack([item]):
            at = AppTest.from_function(_render_review)
            at.run()
        assert not at.exception
        labels = [b.label for b in at.button]
        assert any('Approve' in l for l in labels)
        assert any('Reject' in l for l in labels)
        assert any('Edit' in l for l in labels)

    def test_similar_past_actions_key_renders_correctly(self):
        """REVIEW tab must use reason_json['similar_past_actions'], NOT old 'similar_approvals'."""
        item = _item()
        item['reason_json']['similar_past_actions'] = [
            {'action': 'archive', 'subject': 'Prior email'}
        ]
        with _review_stack([item]):
            at = AppTest.from_function(_render_review)
            at.run()
        assert not at.exception
        all_md = ' '.join(el.value for el in at.markdown)
        assert 'Similar Past Actions' in all_md
        # Source-level guard: confirm the wrong key is absent from app.py
        src = (pathlib.Path(__file__).parent.parent / 'dashboard' / 'app.py').read_text()
        assert "reason.get('similar_approvals')" not in src
        assert 'reason.get("similar_approvals")' not in src

    def test_race_condition_approve_shows_warning(self):
        item = _item()
        with _review_stack([item], approve_ret=False):
            at = AppTest.from_function(_render_review)
            at.run()
            assert not at.exception
            approve_btns = [b for b in at.button if 'Approve' in b.label]
            assert len(approve_btns) >= 1
            approve_btns[0].click()
            at.run()
        assert not at.exception
        assert len(at.warning) >= 1

    def test_race_condition_reject_shows_warning(self):
        item = _item()
        with _review_stack([item], reject_ret=False):
            at = AppTest.from_function(_render_review)
            at.run()
            assert not at.exception
            reject_btns = [b for b in at.button if 'Reject' in b.label]
            assert len(reject_btns) >= 1
            reject_btns[0].click()
            at.run()
        assert not at.exception
        assert len(at.warning) >= 1

    def test_trust_tier_shown_in_review(self):
        item = _item(trust_tier='trusted')
        with _review_stack([item]):
            at = AppTest.from_function(_render_review)
            at.run()
        assert not at.exception
        all_md = ' '.join(el.value for el in at.markdown)
        assert 'trusted' in all_md.lower()

    def test_confidence_shown_in_review(self):
        item = _item(confidence=0.92)
        with _review_stack([item]):
            at = AppTest.from_function(_render_review)
            at.run()
        assert not at.exception
        all_md = ' '.join(el.value for el in at.markdown)
        assert '92' in all_md or '0.92' in all_md
