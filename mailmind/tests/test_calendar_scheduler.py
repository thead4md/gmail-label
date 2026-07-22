"""Tests for deadline -> calendar hold auto-scheduling (client-strategy
reframe §4.4): queries.py's calendar_holds helpers and
intelligence/calendar_scheduler.py.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction
from mailmind.storage.queries import (
    create_calendar_hold, get_calendar_hold, get_calendar_hold_for_email,
    get_pending_calendar_holds, update_calendar_hold_status,
    toggle_sender_auto_calendar, is_sender_auto_calendar_eligible,
)
from mailmind.intelligence.calendar_scheduler import propose_holds_for_email, run_calendar_propose_sweep

DAY = 86400


@pytest.fixture
def db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    database = Database(db_path)
    yield database
    database.close()
    db_path.unlink(missing_ok=True)


def _seed_prediction_with_deadlines(db, gmail_id, sender, subject, date_ts,
                                     deadlines, account=None):
    db.insert_email(Email(
        gmail_id=gmail_id, thread_id=gmail_id, sender=sender, subject=subject,
        snippet="s", date_ts=date_ts, account=account,
    ))
    ctx = json.dumps({"action_items": [], "deadlines": deadlines, "is_thread": False,
                      "thread_length": 1, "reply_needed": False,
                      "open_question_detected": False, "waiting_on_other_party": False})
    db.save_prediction(Prediction(
        email_gmail_id=gmail_id, model="rules", labels=[], priority_score=50,
        thread_context_json=ctx, account=account,
        created_at=date_ts,
    ))


# --------------------------------------------------------------------------- #
# Query contract
# --------------------------------------------------------------------------- #
class TestCalendarHoldQueries:
    def test_create_and_get(self, db):
        hid = create_calendar_hold(db, account="me", email_gmail_id="e1", deadline_text="tomorrow",
                                    summary="Deadline: Report", start_ts=1000, end_ts=1900)
        hold = get_calendar_hold(db, hid)
        assert hold["status"] == "proposed"
        assert hold["summary"] == "Deadline: Report"

    def test_idempotent_per_email_and_deadline_text(self, db):
        id1 = create_calendar_hold(db, account="me", email_gmail_id="e1", deadline_text="tomorrow",
                                    summary="s", start_ts=1000, end_ts=1900)
        id2 = create_calendar_hold(db, account="me", email_gmail_id="e1", deadline_text="tomorrow",
                                    summary="s", start_ts=1000, end_ts=1900)
        assert id1 == id2

    def test_idempotent_with_no_account_configured(self, db):
        id1 = create_calendar_hold(db, account=None, email_gmail_id="e1", deadline_text="tomorrow",
                                    summary="s", start_ts=1000, end_ts=1900)
        id2 = create_calendar_hold(db, account=None, email_gmail_id="e1", deadline_text="tomorrow",
                                    summary="s", start_ts=1000, end_ts=1900)
        assert id1 == id2

    def test_different_deadline_text_same_email_creates_separate_holds(self, db):
        id1 = create_calendar_hold(db, account="me", email_gmail_id="e1", deadline_text="tomorrow",
                                    summary="s", start_ts=1000, end_ts=1900)
        id2 = create_calendar_hold(db, account="me", email_gmail_id="e1", deadline_text="Friday",
                                    summary="s", start_ts=2000, end_ts=2900)
        assert id1 != id2

    def test_get_pending_only_returns_proposed(self, db):
        id1 = create_calendar_hold(db, account="me", email_gmail_id="e1", deadline_text="tomorrow",
                                    summary="s", start_ts=1000, end_ts=1900)
        id2 = create_calendar_hold(db, account="me", email_gmail_id="e2", deadline_text="Friday",
                                    summary="s", start_ts=2000, end_ts=2900)
        update_calendar_hold_status(db, id2, "created", gcal_event_id="gcal-1")
        pending = get_pending_calendar_holds(db, account="me")
        assert [h["id"] for h in pending] == [id1]

    def test_update_status_sets_gcal_event_id(self, db):
        hid = create_calendar_hold(db, account="me", email_gmail_id="e1", deadline_text="tomorrow",
                                    summary="s", start_ts=1000, end_ts=1900)
        update_calendar_hold_status(db, hid, "created", gcal_event_id="gcal-99")
        hold = get_calendar_hold(db, hid)
        assert hold["status"] == "created"
        assert hold["gcal_event_id"] == "gcal-99"

    def test_get_calendar_hold_for_email_excludes_discarded(self, db):
        hid = create_calendar_hold(db, account="me", email_gmail_id="e1", deadline_text="tomorrow",
                                    summary="s", start_ts=1000, end_ts=1900)
        update_calendar_hold_status(db, hid, "discarded")
        assert get_calendar_hold_for_email(db, "e1") is None

    def test_get_calendar_hold_for_email_returns_most_recent(self, db):
        create_calendar_hold(db, account="me", email_gmail_id="e1", deadline_text="tomorrow",
                              summary="s1", start_ts=1000, end_ts=1900)
        hid2 = create_calendar_hold(db, account="me", email_gmail_id="e1", deadline_text="Friday",
                                     summary="s2", start_ts=2000, end_ts=2900)
        result = get_calendar_hold_for_email(db, "e1")
        assert result["id"] == hid2


class TestAutoCalendarEligibility:
    def test_default_not_eligible(self, db):
        assert is_sender_auto_calendar_eligible(db, "bob@y.com") is False

    def test_toggle(self, db):
        toggle_sender_auto_calendar(db, "bob@y.com", True)
        assert is_sender_auto_calendar_eligible(db, "bob@y.com") is True

    def test_independent_of_nudge_and_label_autopilot(self, db):
        from mailmind.storage.queries import toggle_sender_auto_nudge, toggle_sender_auto_action

        toggle_sender_auto_action(db, "bob@y.com", True)
        toggle_sender_auto_nudge(db, "bob@y.com", True)
        assert is_sender_auto_calendar_eligible(db, "bob@y.com") is False


# --------------------------------------------------------------------------- #
# propose_holds_for_email (pure-ish)
# --------------------------------------------------------------------------- #
class TestProposeHoldsForEmail:
    def test_resolvable_deadline_creates_hold(self, db):
        ids = propose_holds_for_email(db, "e1", "me", "Report", ["tomorrow"], now_ts=1_000_000_000)
        assert len(ids) == 1
        hold = get_calendar_hold(db, ids[0])
        assert hold["status"] == "proposed"

    def test_unresolvable_deadline_is_skipped(self, db):
        ids = propose_holds_for_email(db, "e1", "me", "Report", ["ASAP"], now_ts=1_000_000_000)
        assert ids == []

    def test_multiple_deadlines_mixed_resolvability(self, db):
        ids = propose_holds_for_email(db, "e1", "me", "Report", ["ASAP", "tomorrow"], now_ts=1_000_000_000)
        assert len(ids) == 1  # only "tomorrow" resolved


# --------------------------------------------------------------------------- #
# run_calendar_propose_sweep (integration)
# --------------------------------------------------------------------------- #
class TestRunCalendarProposeSweep:
    def test_proposes_holds_for_detected_deadlines(self, db):
        _seed_prediction_with_deadlines(db, "e1", "bob@y.com", "Report", 1_000_000_000,
                                        deadlines=["tomorrow"], account="me")
        res = run_calendar_propose_sweep(db, lambda acct: None, account="me", now_ts=1_000_000_000)
        assert res == {"proposed": 1, "auto_created": 0, "create_failed": 0}
        pending = get_pending_calendar_holds(db, account="me")
        assert len(pending) == 1

    def test_no_deadlines_produces_nothing(self, db):
        _seed_prediction_with_deadlines(db, "e1", "bob@y.com", "Report", 1_000_000_000,
                                        deadlines=[], account="me")
        res = run_calendar_propose_sweep(db, lambda acct: None, account="me", now_ts=1_000_000_000)
        assert res == {"proposed": 0, "auto_created": 0, "create_failed": 0}

    def test_rescanning_same_email_does_not_reduplicate(self, db):
        _seed_prediction_with_deadlines(db, "e1", "bob@y.com", "Report", 1_000_000_000,
                                        deadlines=["tomorrow"], account="me")
        run_calendar_propose_sweep(db, lambda acct: None, account="me", now_ts=1_000_000_000)
        run_calendar_propose_sweep(db, lambda acct: None, account="me", now_ts=1_000_000_000)
        assert len(get_pending_calendar_holds(db, account="me")) == 1

    def test_eligible_sender_auto_creates_event(self, db):
        _seed_prediction_with_deadlines(db, "e1", "bob@y.com", "Report", 1_000_000_000,
                                        deadlines=["tomorrow"], account="me")
        toggle_sender_auto_calendar(db, "bob@y.com", True)

        fake_client = MagicMock()
        fake_client.create_event.return_value = "gcal-evt-1"

        res = run_calendar_propose_sweep(db, lambda acct: fake_client, account="me", now_ts=1_000_000_000)
        assert res == {"proposed": 1, "auto_created": 1, "create_failed": 0}
        assert get_pending_calendar_holds(db, account="me") == []
        fake_client.create_event.assert_called_once()

    def test_ineligible_sender_stays_proposed_even_with_client_available(self, db):
        _seed_prediction_with_deadlines(db, "e1", "bob@y.com", "Report", 1_000_000_000,
                                        deadlines=["tomorrow"], account="me")
        fake_client = MagicMock()
        res = run_calendar_propose_sweep(db, lambda acct: fake_client, account="me", now_ts=1_000_000_000)
        assert res == {"proposed": 1, "auto_created": 0, "create_failed": 0}
        fake_client.create_event.assert_not_called()

    def test_no_client_available_leaves_hold_proposed(self, db):
        _seed_prediction_with_deadlines(db, "e1", "bob@y.com", "Report", 1_000_000_000,
                                        deadlines=["tomorrow"], account="me")
        toggle_sender_auto_calendar(db, "bob@y.com", True)
        res = run_calendar_propose_sweep(db, lambda acct: None, account="me", now_ts=1_000_000_000)
        assert res == {"proposed": 1, "auto_created": 0, "create_failed": 0}
        assert len(get_pending_calendar_holds(db, account="me")) == 1

    def test_create_event_failure_marks_create_failed(self, db):
        _seed_prediction_with_deadlines(db, "e1", "bob@y.com", "Report", 1_000_000_000,
                                        deadlines=["tomorrow"], account="me")
        toggle_sender_auto_calendar(db, "bob@y.com", True)
        fake_client = MagicMock()
        fake_client.create_event.return_value = None  # simulates insufficient-scope failure

        res = run_calendar_propose_sweep(db, lambda acct: fake_client, account="me", now_ts=1_000_000_000)
        assert res == {"proposed": 1, "auto_created": 0, "create_failed": 1}
