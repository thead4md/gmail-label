"""Tests for Loop Radar (mailmind/intelligence/loop_radar.py): the
autonomous follow-up closer for stale 'waiting_on' loops.

All LLM calls are mocked and all sends go through a fake executor or a
monkeypatched handle_approve_and_send — no real API calls are made, per
project convention.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mailmind.intelligence.loop_radar import (
    draft_nudge, run_loop_radar_sweep, MAX_AUTO_NUDGES, NUDGE_COOLDOWN_DAYS,
)
from mailmind.storage.database import Database
from mailmind.storage.queries import (
    upsert_loop, get_open_loops, toggle_sender_auto_nudge, link_loop_draft,
    create_draft, get_draft,
)

DAY = 86400


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def _mock_deepseek_shaped_client(content="Just checking in — any update on this?"):
    """Same MagicMock shape as test_draft_reply.py's helper."""
    client = MagicMock()
    client.model = "deepseek-chat"
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    response.usage = MagicMock(prompt_tokens=80, completion_tokens=20)
    client.client.chat.completions.create.return_value = response
    return client, response


# --------------------------------------------------------------------------- #
# draft_nudge
# --------------------------------------------------------------------------- #
class TestDraftNudge:
    def test_successful_draft(self, db):
        llm_client, _ = _mock_deepseek_shaped_client("Hi Bob, just following up on this!")
        loop = {"subject": "Budget approval", "contact_name": "Bob", "waiting_days": 4}
        result = draft_nudge(db, llm_client, loop)
        assert result == "Hi Bob, just following up on this!"
        llm_client.client.chat.completions.create.assert_called_once()

    def test_llm_client_none_returns_none(self, db):
        loop = {"subject": "hi", "contact_name": "Bob"}
        assert draft_nudge(db, None, loop) is None

    def test_daily_cost_cap_blocks_generation(self, db, monkeypatch):
        llm_client, _ = _mock_deepseek_shaped_client()
        monkeypatch.setattr(
            "mailmind.storage.queries.analytics_llm_cost",
            lambda *a, **k: {"cost_usd": 0.75},
        )
        loop = {"subject": "hi", "contact_name": "Bob"}
        result = draft_nudge(db, llm_client, loop, daily_cost_cap_usd=0.50)
        assert result is None
        llm_client.client.chat.completions.create.assert_not_called()

    def test_llm_exception_returns_none_gracefully(self, db):
        llm_client = MagicMock()
        llm_client.model = "deepseek-chat"
        llm_client.client.chat.completions.create.side_effect = RuntimeError("boom")
        loop = {"subject": "hi", "contact_name": "Bob"}
        assert draft_nudge(db, llm_client, loop) is None

    def test_empty_response_returns_none(self, db):
        llm_client, _ = _mock_deepseek_shaped_client(content="")
        loop = {"subject": "hi", "contact_name": "Bob"}
        assert draft_nudge(db, llm_client, loop) is None

    def test_hungarian_contact_requests_hungarian_nudge(self, db):
        llm_client, _ = _mock_deepseek_shaped_client("Szia, csak érdeklődnék.")
        loop = {"subject": "Üdvözlet", "contact_name": "Kovács Ádám"}
        draft_nudge(db, llm_client, loop)
        call_kwargs = llm_client.client.chat.completions.create.call_args.kwargs
        assert "Hungarian" in call_kwargs["messages"][0]["content"]

    def test_usage_recorded_with_loop_radar_kind(self, db):
        llm_client, _ = _mock_deepseek_shaped_client("Following up.")
        loop = {"subject": "hi", "contact_name": "Bob"}
        draft_nudge(db, llm_client, loop)
        row = db.execute_sql("SELECT * FROM llm_usage WHERE kind = 'loop_radar_nudge'").fetchone()
        assert row is not None

    def test_waiting_days_computed_when_not_provided(self, db):
        llm_client, _ = _mock_deepseek_shaped_client("Following up.")
        loop = {"subject": "hi", "contact_name": "Bob", "last_activity_ts": 0}
        draft_nudge(db, llm_client, loop)
        call_kwargs = llm_client.client.chat.completions.create.call_args.kwargs
        # Just assert it didn't crash and produced a plausible prompt mentioning days.
        assert "day(s)" in call_kwargs["messages"][1]["content"]

    def test_voice_examples_included_when_history_exists(self, db):
        from mailmind.storage.models import Email

        db.insert_email(Email(
            gmail_id="sent1", thread_id="t1", sender="me@x.com", recipients=["bob@y.com"],
            subject="s", snippet="s", body_text="Thanks Bob, talk soon!", date_ts=100,
            labels=["SENT"],
        ))
        llm_client, _ = _mock_deepseek_shaped_client("Following up.")
        loop = {"subject": "Budget", "contact_email": "bob@y.com", "contact_name": "Bob"}
        draft_nudge(db, llm_client, loop)
        call_kwargs = llm_client.client.chat.completions.create.call_args.kwargs
        user_msg = call_kwargs["messages"][1]["content"]
        assert "Thanks Bob, talk soon!" in user_msg


# --------------------------------------------------------------------------- #
# run_loop_radar_sweep
# --------------------------------------------------------------------------- #
class TestRunLoopRadarSweep:
    def _make_open_slipping_loop(self, db, contact_email="bob@y.com"):
        return upsert_loop(
            db, account="me@x.com", thread_id="t1", contact_email=contact_email,
            subject="Budget", last_activity_ts=0, due_ts=0,  # already past due at any now_ts > 0
        )

    def test_not_yet_due_loop_is_skipped(self, db, monkeypatch):
        upsert_loop(db, account="me@x.com", thread_id="t1", contact_email="bob@y.com",
                    last_activity_ts=100, due_ts=10_000)  # due in the future
        drafted = MagicMock(return_value="a nudge")
        monkeypatch.setattr("mailmind.intelligence.loop_radar.draft_nudge", drafted)
        res = run_loop_radar_sweep(db, None, lambda acct: None, account="me@x.com", now_ts=200)
        assert res == {"drafted": 0, "auto_sent": 0, "escalated": 0, "skipped": 0}
        drafted.assert_not_called()

    def test_no_contact_email_is_skipped(self, db, monkeypatch):
        self._make_open_slipping_loop(db, contact_email=None)
        drafted = MagicMock(return_value="a nudge")
        monkeypatch.setattr("mailmind.intelligence.loop_radar.draft_nudge", drafted)
        res = run_loop_radar_sweep(db, None, lambda acct: None, account="me@x.com", now_ts=1000)
        assert res["skipped"] == 1
        drafted.assert_not_called()

    def test_draft_failure_counts_as_skipped(self, db, monkeypatch):
        self._make_open_slipping_loop(db)
        monkeypatch.setattr("mailmind.intelligence.loop_radar.draft_nudge", MagicMock(return_value=None))
        res = run_loop_radar_sweep(db, None, lambda acct: None, account="me@x.com", now_ts=1000)
        assert res["skipped"] == 1
        assert get_open_loops(db, account="me@x.com")[0]["draft_id"] is None

    def test_non_eligible_contact_queues_draft_for_human_review(self, db, monkeypatch):
        lid = self._make_open_slipping_loop(db, contact_email="bob@y.com")
        monkeypatch.setattr("mailmind.intelligence.loop_radar.draft_nudge", MagicMock(return_value="Hi, following up."))
        send_mock = MagicMock()
        monkeypatch.setattr("mailmind.intelligence.feedback.handle_approve_and_send", send_mock)

        res = run_loop_radar_sweep(db, MagicMock(), lambda acct: MagicMock(), account="me@x.com", now_ts=1000)

        assert res == {"drafted": 1, "auto_sent": 0, "escalated": 0, "skipped": 0}
        send_mock.assert_not_called()
        loop = get_open_loops(db, account="me@x.com")[0]
        assert loop["id"] == lid
        assert loop["state"] == "nudge_drafted"
        assert loop["draft_id"] is not None
        draft = get_draft(db, loop["draft_id"])
        assert draft["status"] == "pending_review"
        assert draft["to_addrs"] == "bob@y.com"
        assert draft["generated_by"] == "llm"

    def test_eligible_contact_auto_sends(self, db, monkeypatch):
        self._make_open_slipping_loop(db, contact_email="bob@y.com")
        toggle_sender_auto_nudge(db, "bob@y.com", True)
        monkeypatch.setattr("mailmind.intelligence.loop_radar.draft_nudge", MagicMock(return_value="Hi, following up."))

        send_calls = []

        def _fake_send(db_arg, draft_id_arg, executor_arg):
            send_calls.append((draft_id_arg, executor_arg))
            db_arg.execute_sql("UPDATE drafts SET status='sent' WHERE id=?", (draft_id_arg,))
            return True

        monkeypatch.setattr("mailmind.intelligence.feedback.handle_approve_and_send", _fake_send)
        fake_executor = MagicMock()

        res = run_loop_radar_sweep(db, MagicMock(), lambda acct: fake_executor, account="me@x.com", now_ts=1000)

        assert res == {"drafted": 0, "auto_sent": 1, "escalated": 0, "skipped": 0}
        assert len(send_calls) == 1
        assert send_calls[0][1] is fake_executor
        loop = get_open_loops(db, account="me@x.com")[0]
        assert loop["state"] == "nudge_drafted"  # sweep itself doesn't advance state; the send hook does

    def test_eligible_contact_with_no_executor_falls_back_to_drafted(self, db, monkeypatch):
        self._make_open_slipping_loop(db, contact_email="bob@y.com")
        toggle_sender_auto_nudge(db, "bob@y.com", True)
        monkeypatch.setattr("mailmind.intelligence.loop_radar.draft_nudge", MagicMock(return_value="Hi."))
        send_mock = MagicMock()
        monkeypatch.setattr("mailmind.intelligence.feedback.handle_approve_and_send", send_mock)

        res = run_loop_radar_sweep(db, MagicMock(), lambda acct: None, account="me@x.com", now_ts=1000)

        assert res == {"drafted": 1, "auto_sent": 0, "escalated": 0, "skipped": 0}
        send_mock.assert_not_called()

    def test_eligible_contact_send_failure_falls_back_to_drafted(self, db, monkeypatch):
        self._make_open_slipping_loop(db, contact_email="bob@y.com")
        toggle_sender_auto_nudge(db, "bob@y.com", True)
        monkeypatch.setattr("mailmind.intelligence.loop_radar.draft_nudge", MagicMock(return_value="Hi."))
        monkeypatch.setattr("mailmind.intelligence.feedback.handle_approve_and_send", MagicMock(return_value=False))

        res = run_loop_radar_sweep(db, MagicMock(), lambda acct: MagicMock(), account="me@x.com", now_ts=1000)

        assert res == {"drafted": 1, "auto_sent": 0, "escalated": 0, "skipped": 0}

    def test_nudge_drafted_loop_is_never_touched_again(self, db, monkeypatch):
        lid = self._make_open_slipping_loop(db)
        draft_id = create_draft(db, to_addrs="bob@y.com", subject="Re: Budget", body_text="x")
        link_loop_draft(db, lid, draft_id, state="nudge_drafted")
        drafted = MagicMock(return_value="new nudge")
        monkeypatch.setattr("mailmind.intelligence.loop_radar.draft_nudge", drafted)

        res = run_loop_radar_sweep(db, MagicMock(), lambda acct: MagicMock(), account="me@x.com", now_ts=1000)

        assert res == {"drafted": 0, "auto_sent": 0, "escalated": 0, "skipped": 0}
        drafted.assert_not_called()

    def test_nudged_loop_within_cooldown_is_skipped(self, db, monkeypatch):
        lid = self._make_open_slipping_loop(db)
        link_loop_draft(db, lid, draft_id=1, state="nudged")
        db.execute_sql("UPDATE loops SET nudge_count=1, last_nudge_ts=? WHERE id=?", (1000, lid))
        drafted = MagicMock(return_value="new nudge")
        monkeypatch.setattr("mailmind.intelligence.loop_radar.draft_nudge", drafted)

        now = 1000 + (NUDGE_COOLDOWN_DAYS * DAY) - 10  # just inside the cooldown window
        res = run_loop_radar_sweep(db, MagicMock(), lambda acct: MagicMock(), account="me@x.com", now_ts=now)

        assert res == {"drafted": 0, "auto_sent": 0, "escalated": 0, "skipped": 0}
        drafted.assert_not_called()

    def test_nudged_loop_past_cooldown_is_renudged(self, db, monkeypatch):
        lid = self._make_open_slipping_loop(db)
        link_loop_draft(db, lid, draft_id=1, state="nudged")
        db.execute_sql("UPDATE loops SET nudge_count=1, last_nudge_ts=? WHERE id=?", (1000, lid))
        drafted = MagicMock(return_value="new nudge")
        monkeypatch.setattr("mailmind.intelligence.loop_radar.draft_nudge", drafted)
        monkeypatch.setattr("mailmind.intelligence.feedback.handle_approve_and_send", MagicMock())

        now = 1000 + (NUDGE_COOLDOWN_DAYS * DAY) + 10  # just past the cooldown window
        res = run_loop_radar_sweep(db, MagicMock(), lambda acct: MagicMock(), account="me@x.com", now_ts=now)

        assert res["drafted"] == 1
        drafted.assert_called_once()

    def test_nudged_loop_at_max_nudges_escalates_instead(self, db):
        lid = self._make_open_slipping_loop(db)
        link_loop_draft(db, lid, draft_id=1, state="nudged")
        db.execute_sql(
            "UPDATE loops SET nudge_count=?, last_nudge_ts=? WHERE id=?",
            (MAX_AUTO_NUDGES, 0, lid),
        )
        res = run_loop_radar_sweep(db, MagicMock(), lambda acct: MagicMock(), account="me@x.com", now_ts=1000)
        assert res == {"drafted": 0, "auto_sent": 0, "escalated": 1, "skipped": 0}
        loop = get_open_loops(db, account="me@x.com")[0]
        assert loop["state"] == "escalated"

    def test_escalated_and_closed_loops_are_never_touched(self, db, monkeypatch):
        from mailmind.storage.queries import escalate_loop, close_loop

        lid1 = self._make_open_slipping_loop(db, contact_email="bob@y.com")
        escalate_loop(db, lid1)
        lid2 = upsert_loop(db, account="me@x.com", thread_id="t2", contact_email="carol@y.com", last_activity_ts=0)
        close_loop(db, lid2)

        drafted = MagicMock(return_value="nudge")
        monkeypatch.setattr("mailmind.intelligence.loop_radar.draft_nudge", drafted)
        res = run_loop_radar_sweep(db, MagicMock(), lambda acct: MagicMock(), account="me@x.com", now_ts=1000)
        assert res == {"drafted": 0, "auto_sent": 0, "escalated": 0, "skipped": 0}
        drafted.assert_not_called()


# --------------------------------------------------------------------------- #
# End-to-end: a genuinely sent draft (real handle_approve_and_send, no
# monkeypatch) advances the linked loop's nudge tracking. This exercises the
# actual send-path hook in feedback.handle_approve_and_send, not a stub.
# --------------------------------------------------------------------------- #
class _FakeExecutor:
    """Minimal stand-in for ActionExecutor: handle_approve_and_send only ever
    calls .send_message(draft, raw_mime_b64url) on it."""

    def __init__(self, result="msg-123"):
        self.result = result
        self.calls = []

    def send_message(self, draft, raw_mime_b64url):
        self.calls.append((draft, raw_mime_b64url))
        return self.result


class TestHandleApproveAndSendClosesLoopTracking:
    def test_real_send_marks_linked_loop_nudged(self, db):
        from mailmind.intelligence.feedback import handle_approve_and_send

        lid = upsert_loop(db, account="me@x.com", thread_id="t1", contact_email="bob@y.com",
                           subject="Budget", last_activity_ts=0, due_ts=0)
        draft_id = create_draft(db, account="me@x.com", kind="compose", to_addrs="bob@y.com",
                                 subject="Re: Budget", body_text="Just checking in!", generated_by="llm")
        link_loop_draft(db, lid, draft_id, state="nudge_drafted")

        from mailmind.storage.queries import update_draft_status
        update_draft_status(db, draft_id, "approved")

        executor = _FakeExecutor(result="msg-abc")
        assert handle_approve_and_send(db, draft_id, executor) is True

        draft = get_draft(db, draft_id)
        assert draft["status"] == "sent"
        loop = get_open_loops(db, account="me@x.com")[0]
        assert loop["state"] == "nudged"
        assert loop["nudge_count"] == 1
        assert loop["last_nudge_ts"] is not None

    def test_dry_run_send_still_marks_linked_loop_nudged(self, db):
        # dry_run only suppresses the literal Gmail write, not this system's
        # bookkeeping -- matches every other dry-run code path in this app.
        from mailmind.intelligence.feedback import handle_approve_and_send
        from mailmind.storage.queries import update_draft_status

        lid = upsert_loop(db, account="me@x.com", thread_id="t1", contact_email="bob@y.com",
                           subject="Budget", last_activity_ts=0, due_ts=0)
        draft_id = create_draft(db, account="me@x.com", kind="compose", to_addrs="bob@y.com",
                                 subject="Re: Budget", body_text="Just checking in!", generated_by="llm")
        link_loop_draft(db, lid, draft_id, state="nudge_drafted")
        update_draft_status(db, draft_id, "approved")

        executor = _FakeExecutor(result="dry_run")
        assert handle_approve_and_send(db, draft_id, executor) is True

        loop = get_open_loops(db, account="me@x.com")[0]
        assert loop["state"] == "nudged"
        assert loop["nudge_count"] == 1

    def test_failed_send_does_not_mark_loop_nudged(self, db):
        from mailmind.intelligence.feedback import handle_approve_and_send
        from mailmind.storage.queries import update_draft_status

        lid = upsert_loop(db, account="me@x.com", thread_id="t1", contact_email="bob@y.com",
                           subject="Budget", last_activity_ts=0, due_ts=0)
        draft_id = create_draft(db, account="me@x.com", kind="compose", to_addrs="bob@y.com",
                                 subject="Re: Budget", body_text="Just checking in!", generated_by="llm")
        link_loop_draft(db, lid, draft_id, state="nudge_drafted")
        update_draft_status(db, draft_id, "approved")

        executor = _FakeExecutor(result=None)  # send failure
        assert handle_approve_and_send(db, draft_id, executor) is False

        loop = get_open_loops(db, account="me@x.com")[0]
        assert loop["state"] == "nudge_drafted"  # unchanged
        assert loop["nudge_count"] == 0

    def test_sending_an_unlinked_draft_does_not_crash_or_touch_any_loop(self, db):
        # The overwhelming majority of drafts have no loop attached at all --
        # the bookkeeping hook must be a true no-op for them.
        from mailmind.intelligence.feedback import handle_approve_and_send
        from mailmind.storage.queries import update_draft_status

        lid = upsert_loop(db, account="me@x.com", thread_id="t1", contact_email="carol@y.com", last_activity_ts=0)
        draft_id = create_draft(db, account="me@x.com", kind="reply", to_addrs="dave@z.com",
                                 subject="Hi", body_text="A normal reply, unrelated to any loop.")
        update_draft_status(db, draft_id, "approved")

        executor = _FakeExecutor(result="msg-xyz")
        assert handle_approve_and_send(db, draft_id, executor) is True

        loop = get_open_loops(db, account="me@x.com")[0]
        assert loop["id"] == lid
        assert loop["state"] == "open"  # untouched
        assert loop["nudge_count"] == 0
