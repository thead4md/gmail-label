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
import streamlit as st
from streamlit.testing.v1 import AppTest


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    """Reset @st.cache_data/@st.cache_resource between tests.

    The dashboard now wraps its DB reads in @st.cache_data. Without clearing,
    one test's mocked return value is memoised and served to later tests
    (e.g. the first REVIEW test caches an empty pending queue, so every
    later REVIEW test sees 'Queue is clear'). Clear before AND after each test.
    """
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


def _render_insights():
    from mailmind.dashboard import app as _a  # noqa: PLC0415
    _a.render_insights_tab()


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
    # KPI overview row reads build_digest via _c_digest — stub it so the row
    # renders without touching the mock DB.
    stack.enter_context(
        patch('mailmind.dashboard.app._c_digest', return_value={
            'classified': 12, 'executed': 5, 'queued': 3, 'pending_reply_needed': 2,
        })
    )
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
        # Empty state is rendered as a custom HTML div (not st.info in the redesign)
        all_md = ' '.join(el.value for el in at.markdown)
        assert 'mm-empty' in all_md or 'caught up' in all_md.lower()
        assert len(at.button) == 0

    def test_kpi_overview_row_renders(self):
        with _now_stack([], filter_out=[]):
            at = AppTest.from_function(_render_now)
            at.run()
        assert not at.exception
        all_md = ' '.join(el.value for el in at.markdown)
        assert 'mm-kpi-grid' in all_md
        assert 'Triaged today' in all_md

    def test_single_item_shows_exactly_one_approve_button(self):
        item = _item(priority_score=90)
        item['reason_json']['reply_needed'] = True
        with _now_stack([item]):
            at = AppTest.from_function(_render_now)
            at.run()
        assert not at.exception
        approve_btns = [b for b in at.button if 'Approve' in b.label]
        assert len(approve_btns) == 1

    def test_reject_button_present_in_now_tab(self):
        item = _item(priority_score=90)
        item['reason_json']['reply_needed'] = True
        with _now_stack([item]):
            at = AppTest.from_function(_render_now)
            at.run()
        assert not at.exception
        reject_btns = [b for b in at.button if 'Reject' in b.label]
        assert len(reject_btns) == 1

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
        # The redesign embeds this in the HTML card as "Reply needed" (lowercase n)
        assert 'reply needed' in all_md.lower()

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
        # The redesigned reason panel renders this as "Past actions" (compact label)
        assert 'past actions' in all_md.lower() or 'archive' in all_md.lower()
        # Source-level guard: the old wrong key must not appear in app.py
        src = (pathlib.Path(__file__).parent.parent / 'dashboard' / 'app.py').read_text()
        assert "reason.get('similar_approvals')" not in src
        assert 'reason.get("similar_approvals")' not in src
        # The correct key must be used
        assert 'similar_past_actions' in src

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


# ---------------------------------------------------------------------------
# INSIGHTS tab — regression guard for the missing render_insights_tab bug
# ---------------------------------------------------------------------------

def _insights_stack(rows=None):
    rows = rows if rows is not None else []
    stack = contextlib.ExitStack()
    stack.enter_context(patch('mailmind.dashboard.app.get_db', return_value=MagicMock()))
    for fn in (
        'analytics_label_distribution', 'analytics_channel_distribution',
        'analytics_channel_weekday', 'analytics_top_senders', 'analytics_decision_times',
    ):
        stack.enter_context(patch(f'mailmind.dashboard.app.{fn}', return_value=rows))
    return stack


class TestRenderInsightsTab:

    def test_render_insights_tab_is_defined(self):
        from mailmind.dashboard import app as a
        assert hasattr(a, 'render_insights_tab'), \
            "render_insights_tab must be defined — main() calls it"

    def test_empty_insights_renders_without_exception(self):
        with _insights_stack([]):
            at = AppTest.from_function(_render_insights)
            at.run()
        assert not at.exception
        # Every section falls back to an info message when there's no data
        assert len(at.info) >= 1

    def test_insights_with_data_renders_charts(self):
        # Each analytics fn gets rows shaped the way its chart builder expects.
        returns = {
            'analytics_label_distribution':   [{'label': 'WORK', 'count': 5}],
            'analytics_channel_distribution': [{'channel': 'team', 'count': 4}],
            'analytics_channel_weekday':      [{'channel': 'team', 'weekday': 1, 'count': 2}],
            'analytics_top_senders':          [{'sender': 'a@b.com', 'volume': 3,
                                                'approval_rate': 0.5}],
            'analytics_decision_times':       [{'minutes': 2.0}],
        }
        stack = contextlib.ExitStack()
        stack.enter_context(patch('mailmind.dashboard.app.get_db', return_value=MagicMock()))
        for fn, rows in returns.items():
            stack.enter_context(patch(f'mailmind.dashboard.app.{fn}', return_value=rows))
        with stack:
            at = AppTest.from_function(_render_insights)
            at.run()
        assert not at.exception


# ---------------------------------------------------------------------------
# Magic-display guard (AST): a bare ternary expression-statement like
#   st.altair_chart(c) if c else st.info("...")
# is an ast.Expr whose value is ast.IfExp. Streamlit "magic" wraps non-Call
# expression statements in st.write(); st.write(<DeltaGenerator>) then dumps a
# DeltaGenerator help table to the page. Plain `st.foo(...)` calls are ast.Call
# and are skipped by magic. This guard fails if any bare ternary statement
# sneaks back into app.py.
# ---------------------------------------------------------------------------

def test_no_bare_ternary_expression_statements_in_app():
    import ast
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "dashboard" / "app.py").read_text()
    tree = ast.parse(src)
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.IfExp)
    ]
    assert not offenders, (
        f"Bare ternary expression-statements at lines {offenders} in app.py — "
        "Streamlit magic will wrap these in st.write() and dump DeltaGenerator "
        "help tables. Use an explicit if/else statement instead."
    )


def test_invalidate_helper_exists_and_callable():
    """Every write path must be able to clear caches; _invalidate must exist."""
    from mailmind.dashboard import app as a
    assert hasattr(a, "_invalidate") and callable(a._invalidate)


def test_writes_are_paired_with_invalidate():
    """Each st.rerun() in app.py that follows a write should be preceded by
    _invalidate(). Static check: count must match closely."""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "dashboard" / "app.py").read_text()
    assert src.count("_invalidate()") >= 4  # approve/reject/correct/toggle paths


def test_invalidate_clears_only_queue_caches():
    """Verify _invalidate() clears queue-affected caches but not analytics caches."""
    from mailmind.dashboard import app as a

    # Queue/decision-affected caches that SHOULD be cleared by _invalidate()
    queue_caches = [
        '_c_pending',
        '_c_queue_stats',
        '_c_digest',
        '_c_executed',
        '_c_new_senders',
        '_c_corrections',
    ]

    # Caches that should NOT be cleared by _invalidate() (sender or analytics)
    analytics_caches = [
        '_c_recent_predictions',
        '_c_sender_profiles',
        '_c_label_dist',
        '_c_channel_dist',
        '_c_channel_weekday',
        '_c_top_senders',
        '_c_decision_times',
        '_c_model_metadata',
        '_c_gmail_labels',
    ]

    # Mock all cache functions with .clear() methods
    with patch.object(a, '_c_pending', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_queue_stats', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_digest', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_executed', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_new_senders', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_recent_predictions', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_corrections', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_sender_profiles', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_label_dist', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_channel_dist', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_channel_weekday', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_top_senders', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_decision_times', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_model_metadata', MagicMock(clear=MagicMock())), \
         patch.object(a, '_c_gmail_labels', MagicMock(clear=MagicMock())):

        a._invalidate()

        # Verify queue caches were cleared
        for cache_name in queue_caches:
            cache_obj = getattr(a, cache_name)
            cache_obj.clear.assert_called_once()

        # Verify analytics caches were NOT cleared
        for cache_name in analytics_caches:
            cache_obj = getattr(a, cache_name)
            cache_obj.clear.assert_not_called()
