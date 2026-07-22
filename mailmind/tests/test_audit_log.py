"""Tests for the unified audit log (client-strategy reframe V3 "deeper agent
autonomy" item): queries.get_unified_audit_log, which unions executed
labels (action_queue), sent drafts/nudges (drafts), and created calendar
events (calendar_holds) into one chronological, filterable timeline.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mailmind.storage.database import Database
from mailmind.storage.queries import (
    get_unified_audit_log, create_calendar_hold, update_calendar_hold_status,
    create_draft, update_draft_status,
)


@pytest.fixture
def db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    database = Database(db_path)
    yield database
    database.close()
    db_path.unlink(missing_ok=True)


def _seed_executed_queue_item(db, *, ts, account=None, reviewed_at=None, subject="Report"):
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO emails (gmail_id, subject) VALUES (?, ?)",
            (f"e-{ts}", subject),
        )
        cur.execute(
            """
            INSERT INTO action_queue
                (email_gmail_id, account, action, params_json, action_fingerprint,
                 status, confidence, priority_score, reason_json, created_at,
                 reviewed_at, executed_at)
            VALUES (?, ?, 'label', '{}', ?, 'executed', 0.95, 80, '{}', ?, ?, ?)
            """,
            (f"e-{ts}", account, f"fp-{ts}", ts, reviewed_at, ts),
        )


class TestUnifiedAuditLog:
    def test_empty_when_nothing_executed(self, db):
        assert get_unified_audit_log(db) == []

    def test_includes_auto_executed_label(self, db):
        _seed_executed_queue_item(db, ts=1000, reviewed_at=None)  # no review -> auto
        log = get_unified_audit_log(db)
        assert len(log) == 1
        assert log[0]["kind"] == "label"
        assert log[0]["was_auto"] == 1

    def test_includes_human_approved_label(self, db):
        _seed_executed_queue_item(db, ts=1000, reviewed_at=1000)  # reviewed -> human approved
        log = get_unified_audit_log(db)
        assert log[0]["was_auto"] == 0

    def test_includes_sent_draft_as_human_by_default(self, db):
        draft_id = create_draft(db, to_addrs="bob@y.com", subject="Hi", body_text="x")
        update_draft_status(db, draft_id, "sent", sent_at=1000)
        log = get_unified_audit_log(db)
        assert len(log) == 1
        assert log[0]["kind"] == "sent"
        assert log[0]["was_auto"] == 0  # generated_by defaults to 'human'

    def test_includes_llm_generated_sent_draft_as_auto(self, db):
        draft_id = create_draft(db, to_addrs="bob@y.com", subject="Hi", body_text="x", generated_by="llm")
        update_draft_status(db, draft_id, "sent", sent_at=1000)
        log = get_unified_audit_log(db)
        assert log[0]["was_auto"] == 1

    def test_excludes_non_sent_drafts(self, db):
        create_draft(db, to_addrs="bob@y.com", subject="Hi", body_text="x")  # stays pending_review
        assert get_unified_audit_log(db) == []

    def test_includes_human_created_calendar_hold_as_not_auto(self, db):
        hid = create_calendar_hold(db, account=None, email_gmail_id="e1", deadline_text="tomorrow",
                                    summary="Deadline", start_ts=1000, end_ts=1900)
        update_calendar_hold_status(db, hid, "created", gcal_event_id="gcal-1", created_by="human")
        log = get_unified_audit_log(db)
        assert len(log) == 1
        assert log[0]["kind"] == "calendar"
        assert log[0]["was_auto"] == 0

    def test_includes_auto_created_calendar_hold_as_auto(self, db):
        hid = create_calendar_hold(db, account=None, email_gmail_id="e1", deadline_text="tomorrow",
                                    summary="Deadline", start_ts=1000, end_ts=1900)
        update_calendar_hold_status(db, hid, "created", gcal_event_id="gcal-1", created_by="auto")
        log = get_unified_audit_log(db)
        assert log[0]["was_auto"] == 1

    def test_excludes_non_created_calendar_holds(self, db):
        create_calendar_hold(db, account=None, email_gmail_id="e1", deadline_text="tomorrow",
                              summary="Deadline", start_ts=1000, end_ts=1900)  # stays 'proposed'
        assert get_unified_audit_log(db) == []

    def test_sorted_newest_first_across_all_three_kinds(self, db):
        _seed_executed_queue_item(db, ts=1000, reviewed_at=1000)
        draft_id = create_draft(db, to_addrs="bob@y.com", subject="Hi", body_text="x")
        update_draft_status(db, draft_id, "sent", sent_at=3000)
        hid = create_calendar_hold(db, account=None, email_gmail_id="e1", deadline_text="tomorrow",
                                    summary="Deadline", start_ts=100, end_ts=190)
        update_calendar_hold_status(db, hid, "created", gcal_event_id="g1", created_by="human")
        # Force a distinct updated_at for a deterministic ordering check.
        db.execute_sql("UPDATE calendar_holds SET updated_at = 2000 WHERE id = ?", (hid,))

        log = get_unified_audit_log(db)
        assert [r["kind"] for r in log] == ["sent", "calendar", "label"]

    def test_since_ts_filters_out_older_entries(self, db):
        _seed_executed_queue_item(db, ts=1000, reviewed_at=1000)
        _seed_executed_queue_item(db, ts=5000, reviewed_at=5000)
        log = get_unified_audit_log(db, since_ts=3000)
        assert len(log) == 1

    def test_account_filter(self, db):
        _seed_executed_queue_item(db, ts=1000, account="acct1", reviewed_at=1000)
        _seed_executed_queue_item(db, ts=2000, account="acct2", reviewed_at=2000, subject="Other")
        log = get_unified_audit_log(db, account="acct1")
        assert len(log) == 1

    def test_respects_limit(self, db):
        for i in range(5):
            _seed_executed_queue_item(db, ts=1000 + i, reviewed_at=1000 + i, subject=f"s{i}")
        assert len(get_unified_audit_log(db, limit=2)) == 2
