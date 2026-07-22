"""Tests for open-loop detection (the 'waiting_on' side of the reframe).

Covers the pure core (compute_thread_states), the query contract
(upsert_loop / get_open_loops / close_loop) and the end-to-end detector
(detect_waiting_on_loops) including auto-close when a reply arrives.

Hermetic: temp-file SQLite, no network, no LLM.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mailmind.storage.database import Database
from mailmind.storage.queries import (
    upsert_loop, get_open_loops, close_loop,
    link_loop_draft, mark_loop_nudged_from_draft, escalate_loop,
    is_sender_auto_nudge_eligible, toggle_sender_auto_nudge,
)
from mailmind.intelligence.loops import (
    compute_thread_states,
    detect_waiting_on_loops,
    split_addr,
    _is_outbound,
)

USER = "me@x.com"
USERS = {USER}
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


def _seed_email(
    db: Database,
    *,
    gmail_id: str,
    thread_id: str,
    sender: str,
    date_ts: int,
    labels: str,
    recipients: str = "",
    subject: str = "Subject",
    account: str = USER,
) -> None:
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO emails (gmail_id, thread_id, sender, recipients, subject,"
            " date_ts, labels, account) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (gmail_id, thread_id, sender, recipients, subject, date_ts, labels, account),
        )


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
class TestAddressHelpers:
    def test_split_addr_with_name(self):
        assert split_addr("Bob Smith <bob@y.com>") == ("bob@y.com", "Bob Smith")

    def test_split_addr_bare(self):
        assert split_addr("bob@y.com") == ("bob@y.com", None)

    def test_split_addr_none(self):
        assert split_addr(None) == (None, None)

    def test_is_outbound_by_sent_label(self):
        assert _is_outbound({"labels": "SENT,IMPORTANT", "sender": "anyone@z.com"}, USERS)

    def test_is_outbound_by_sender(self):
        assert _is_outbound({"labels": "INBOX", "sender": f"Me <{USER}>"}, USERS)

    def test_inbound_is_not_outbound(self):
        assert not _is_outbound({"labels": "INBOX", "sender": "bob@y.com"}, USERS)

    def test_unrelated_sender_containing_user_address_as_substring_is_not_outbound(self):
        # "awesome@x.com" contains the literal substring "me@x.com" — a raw
        # `addr in sender` check would misclassify this unrelated inbound
        # sender as outbound. Exact parsed-address match must reject it.
        assert "me@x.com" in "awesome@x.com"  # sanity-check the collision is real
        assert not _is_outbound({"labels": "INBOX", "sender": "awesome@x.com"}, USERS)


# --------------------------------------------------------------------------- #
# Pure core
# --------------------------------------------------------------------------- #
class TestComputeThreadStates:
    def test_outbound_newest_opens_loop(self):
        emails = [
            {"thread_id": "t1", "sender": "bob@y.com", "labels": "INBOX", "date_ts": 100, "subject": "Q"},
            {"thread_id": "t1", "sender": USER, "labels": "SENT", "date_ts": 200, "recipients": "bob@y.com", "subject": "Re: Q"},
        ]
        loops, replied = compute_thread_states(emails, USERS, now_ts=300)
        assert replied == set()
        assert len(loops) == 1
        lp = loops[0]
        assert lp["thread_id"] == "t1"
        assert lp["contact_email"] == "bob@y.com"
        assert lp["last_sent_ts"] == 200
        assert lp["due_ts"] == 200 + 2 * DAY  # default stale_after_days=2

    def test_inbound_newest_is_replied(self):
        emails = [
            {"thread_id": "t2", "sender": USER, "labels": "SENT", "date_ts": 100, "recipients": "bob@y.com"},
            {"thread_id": "t2", "sender": "bob@y.com", "labels": "INBOX", "date_ts": 200},
        ]
        loops, replied = compute_thread_states(emails, USERS, now_ts=300)
        assert loops == []
        assert replied == {"t2"}

    def test_cold_outbound_only_thread_opens_loop_with_recipient_contact(self):
        emails = [
            {"thread_id": "t3", "sender": USER, "labels": "SENT", "date_ts": 100, "recipients": "Alice <alice@z.com>, x@z.com"},
        ]
        loops, replied = compute_thread_states(emails, USERS, now_ts=300)
        assert len(loops) == 1
        assert loops[0]["contact_email"] == "alice@z.com"
        assert loops[0]["contact_name"] == "Alice"

    def test_email_without_thread_id_is_ignored(self):
        emails = [{"thread_id": None, "sender": USER, "labels": "SENT", "date_ts": 100}]
        loops, replied = compute_thread_states(emails, USERS, now_ts=300)
        assert loops == [] and replied == set()


# --------------------------------------------------------------------------- #
# Query contract
# --------------------------------------------------------------------------- #
class TestLoopQueries:
    def test_upsert_is_idempotent_per_thread_side(self, db):
        id1 = upsert_loop(db, account=USER, thread_id="t1", contact_email="bob@y.com", last_activity_ts=100)
        id2 = upsert_loop(db, account=USER, thread_id="t1", contact_email="bob@y.com", last_activity_ts=200)
        assert id1 == id2
        loops = get_open_loops(db, account=USER)
        assert len(loops) == 1
        assert loops[0]["last_activity_ts"] == 200

    def test_get_open_loops_orders_stalest_first(self, db):
        upsert_loop(db, account=USER, thread_id="fresh", last_activity_ts=500)
        upsert_loop(db, account=USER, thread_id="stale", last_activity_ts=100)
        loops = get_open_loops(db, account=USER)
        assert [l["thread_id"] for l in loops] == ["stale", "fresh"]

    def test_close_loop(self, db):
        lid = upsert_loop(db, account=USER, thread_id="t1", last_activity_ts=100)
        assert close_loop(db, lid) is True
        assert get_open_loops(db, account=USER) == []
        assert close_loop(db, lid) is False  # already closed

    def test_upsert_is_idempotent_with_no_account_configured(self, db):
        # SQLite's UNIQUE index treats NULL as distinct from every other NULL,
        # so account=None (the watch loop's fallback when no mailbox is
        # configured at all) must be normalized before it reaches the
        # UNIQUE(account, thread_id, side) constraint, or every repeated
        # detection would insert a fresh duplicate row instead of updating.
        id1 = upsert_loop(db, account=None, thread_id="t1", last_activity_ts=100)
        id2 = upsert_loop(db, account=None, thread_id="t1", last_activity_ts=200)
        id3 = upsert_loop(db, account=None, thread_id="t1", last_activity_ts=300)
        assert id1 == id2 == id3
        loops = get_open_loops(db, account=None)
        assert len(loops) == 1
        assert loops[0]["last_activity_ts"] == 300


# --------------------------------------------------------------------------- #
# End-to-end detector
# --------------------------------------------------------------------------- #
class TestDetectWaitingOnLoops:
    def test_detects_sent_without_reply(self, db):
        _seed_email(db, gmail_id="m1", thread_id="t1", sender="bob@y.com", date_ts=1000, labels="INBOX")
        _seed_email(db, gmail_id="m2", thread_id="t1", sender=USER, date_ts=2000, labels="SENT", recipients="bob@y.com")
        res = detect_waiting_on_loops(db, account=USER, now_ts=3000)
        assert res["open"] == 1
        loops = get_open_loops(db, account=USER)
        assert len(loops) == 1
        assert loops[0]["contact_email"] == "bob@y.com"

    def test_reply_closes_existing_loop(self, db):
        _seed_email(db, gmail_id="m2", thread_id="t1", sender=USER, date_ts=2000, labels="SENT", recipients="bob@y.com")
        detect_waiting_on_loops(db, account=USER, now_ts=3000)
        assert len(get_open_loops(db, account=USER)) == 1

        # Bob replies -> newest message is now inbound -> loop should close.
        _seed_email(db, gmail_id="m3", thread_id="t1", sender="bob@y.com", date_ts=4000, labels="INBOX")
        res = detect_waiting_on_loops(db, account=USER, now_ts=5000)
        assert res["closed"] == 1
        assert get_open_loops(db, account=USER) == []

    def test_slipping_via_due_ts(self, db):
        # Sent long ago (well past the 2-day default stale window).
        _seed_email(db, gmail_id="m2", thread_id="t1", sender=USER, date_ts=1000, labels="SENT", recipients="bob@y.com")
        detect_waiting_on_loops(db, account=USER, now_ts=1000 + 10 * DAY)
        loops = get_open_loops(db, account=USER)
        assert loops[0]["due_ts"] < 1000 + 10 * DAY  # due date is in the past => slipping

    def test_reply_from_substring_colliding_sender_still_closes_loop(self, db):
        # USER is "me@x.com"; "awesome@x.com" contains "me@x.com" as a literal
        # substring. A raw substring-based outbound check would misclassify
        # Bob's reply as outbound and the loop would never close.
        assert "me@x.com" in "awesome@x.com"
        _seed_email(db, gmail_id="m2", thread_id="t1", sender=USER, date_ts=1000, labels="SENT", recipients="awesome@x.com")
        detect_waiting_on_loops(db, account=USER, now_ts=2000)
        assert len(get_open_loops(db, account=USER)) == 1

        _seed_email(db, gmail_id="m3", thread_id="t1", sender="awesome@x.com", date_ts=3000, labels="INBOX")
        res = detect_waiting_on_loops(db, account=USER, now_ts=4000)
        assert res["closed"] == 1
        assert get_open_loops(db, account=USER) == []

    def test_repeated_sweeps_with_no_account_configured_do_not_duplicate(self, db):
        _seed_email(db, gmail_id="m2", thread_id="t1", sender=USER, date_ts=1000, labels="SENT", recipients="bob@y.com", account=None)
        for _ in range(3):
            detect_waiting_on_loops(db, account=None, user_addresses=USERS, now_ts=2000)
        loops = get_open_loops(db, account=None)
        assert len(loops) == 1


# --------------------------------------------------------------------------- #
# Loop Radar state machine: link_loop_draft / mark_loop_nudged_from_draft /
# escalate_loop, and the passive detector's interaction with active states.
# --------------------------------------------------------------------------- #
class TestLoopRadarStateMachine:
    def test_link_loop_draft_sets_draft_id_and_state(self, db):
        lid = upsert_loop(db, account=USER, thread_id="t1", last_activity_ts=100)
        assert link_loop_draft(db, lid, draft_id=42, state="nudge_drafted") is True
        loop = get_open_loops(db, account=USER)[0]
        assert loop["draft_id"] == 42
        assert loop["state"] == "nudge_drafted"

    def test_mark_loop_nudged_from_draft_updates_state_and_count(self, db):
        lid = upsert_loop(db, account=USER, thread_id="t1", last_activity_ts=100)
        link_loop_draft(db, lid, draft_id=42, state="nudge_drafted")
        assert mark_loop_nudged_from_draft(db, draft_id=42) is True
        loop = get_open_loops(db, account=USER)[0]
        assert loop["state"] == "nudged"
        assert loop["nudge_count"] == 1
        assert loop["last_nudge_ts"] is not None

    def test_mark_loop_nudged_from_draft_is_noop_for_unlinked_draft(self, db):
        upsert_loop(db, account=USER, thread_id="t1", last_activity_ts=100)
        assert mark_loop_nudged_from_draft(db, draft_id=999) is False

    def test_escalate_loop(self, db):
        lid = upsert_loop(db, account=USER, thread_id="t1", last_activity_ts=100)
        assert escalate_loop(db, lid) is True
        loop = get_open_loops(db, account=USER)[0]
        assert loop["state"] == "escalated"

    def test_escalate_loop_is_noop_once_closed(self, db):
        lid = upsert_loop(db, account=USER, thread_id="t1", last_activity_ts=100)
        close_loop(db, lid)
        assert escalate_loop(db, lid) is False

    def test_get_open_loops_includes_active_non_open_states(self, db):
        # nudge_drafted/nudged/escalated must still surface as "active" so the
        # UI keeps showing them and the passive detector keeps scanning them
        # for a reply — only closed/snoozed should disappear.
        lid = upsert_loop(db, account=USER, thread_id="t1", last_activity_ts=100)
        link_loop_draft(db, lid, draft_id=1, state="nudged")
        loops = get_open_loops(db, account=USER)
        assert len(loops) == 1 and loops[0]["state"] == "nudged"

    def test_passive_detector_does_not_reset_nudged_state_to_open(self, db):
        # A loop already in an active Radar state must not be silently reset
        # to 'open' just because its own outbound nudge is now the thread's
        # newest message — that would erase the Radar's tracking every sweep.
        _seed_email(db, gmail_id="m1", thread_id="t1", sender="bob@y.com", date_ts=1000, labels="INBOX")
        _seed_email(db, gmail_id="m2", thread_id="t1", sender=USER, date_ts=2000, labels="SENT", recipients="bob@y.com")
        detect_waiting_on_loops(db, account=USER, now_ts=3000)
        lid = get_open_loops(db, account=USER)[0]["id"]
        link_loop_draft(db, lid, draft_id=1, state="nudged")

        # Our own nudge lands as a new SENT message in the same thread — the
        # detector re-scans and would normally re-upsert this thread.
        _seed_email(db, gmail_id="m3", thread_id="t1", sender=USER, date_ts=4000, labels="SENT", recipients="bob@y.com")
        detect_waiting_on_loops(db, account=USER, now_ts=5000)

        loop = get_open_loops(db, account=USER)[0]
        assert loop["state"] == "nudged"  # NOT reset to 'open'

    def test_passive_detector_reopens_a_closed_loop_as_open(self, db):
        # A genuinely NEW commitment cycle (closed, then the user sends
        # another outbound message later) should reopen as 'open', not
        # inherit the stale 'closed' state.
        _seed_email(db, gmail_id="m1", thread_id="t1", sender=USER, date_ts=1000, labels="SENT", recipients="bob@y.com")
        detect_waiting_on_loops(db, account=USER, now_ts=2000)
        lid = get_open_loops(db, account=USER)[0]["id"]
        _seed_email(db, gmail_id="m2", thread_id="t1", sender="bob@y.com", date_ts=3000, labels="INBOX")
        detect_waiting_on_loops(db, account=USER, now_ts=4000)
        assert get_open_loops(db, account=USER) == []  # closed by the reply

        _seed_email(db, gmail_id="m3", thread_id="t1", sender=USER, date_ts=5000, labels="SENT", recipients="bob@y.com")
        detect_waiting_on_loops(db, account=USER, now_ts=6000)
        loops = get_open_loops(db, account=USER)
        assert len(loops) == 1
        assert loops[0]["id"] == lid
        assert loops[0]["state"] == "open"


class TestAutoNudgeEligibility:
    def test_default_is_not_eligible(self, db):
        assert is_sender_auto_nudge_eligible(db, "bob@y.com") is False

    def test_toggle_on_then_off(self, db):
        toggle_sender_auto_nudge(db, "bob@y.com", True)
        assert is_sender_auto_nudge_eligible(db, "bob@y.com") is True
        toggle_sender_auto_nudge(db, "bob@y.com", False)
        assert is_sender_auto_nudge_eligible(db, "bob@y.com") is False

    def test_auto_nudge_is_independent_of_label_autopilot(self, db):
        # Granting one must never silently grant the other -- they're
        # deliberately separate flags for a reason: sending new outbound
        # content is materially more consequential than applying a label.
        from mailmind.storage.queries import toggle_sender_auto_action, is_sender_auto_action_eligible

        toggle_sender_auto_action(db, "bob@y.com", True)
        assert is_sender_auto_action_eligible(db, "bob@y.com") is True
        assert is_sender_auto_nudge_eligible(db, "bob@y.com") is False

    def test_no_sender_email_is_not_eligible(self, db):
        assert is_sender_auto_nudge_eligible(db, None) is False
        assert is_sender_auto_nudge_eligible(db, "") is False
