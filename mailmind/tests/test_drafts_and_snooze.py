"""Tests for the drafts + action-queue-snooze query contract:
create_draft, get_draft, update_draft_status, get_due_scheduled_drafts,
snooze_queue_item, get_due_snoozed_items, unsnooze_queue_item.

Three other agents build the send gate / compose UI / scheduler sweep against
these exact signatures, so these tests pin down filtering/status-transition
behavior.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mailmind.storage.database import Database
from mailmind.storage.queries import (
    create_draft,
    get_draft,
    update_draft_status,
    get_due_scheduled_drafts,
    snooze_queue_item,
    get_due_snoozed_items,
    unsnooze_queue_item,
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


def _seed_queue_item(db: Database, fingerprint: str, email_gmail_id: str = "e1") -> int:
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO action_queue (email_gmail_id, action, action_fingerprint)"
            " VALUES (?, 'archive', ?)",
            (email_gmail_id, fingerprint),
        )
        return int(cur.lastrowid)


class TestCreateAndGetDraft:
    def test_create_draft_returns_int_id(self, db):
        draft_id = create_draft(
            db, to_addrs="a@b.com", subject="Hi", body_text="Body text"
        )
        assert isinstance(draft_id, int)
        assert draft_id > 0

    def test_get_draft_returns_none_when_missing(self, db):
        assert get_draft(db, 999) is None

    def test_get_draft_returns_full_row_with_defaults(self, db):
        draft_id = create_draft(
            db, to_addrs="a@b.com", subject="Hi", body_text="Body"
        )
        row = get_draft(db, draft_id)
        assert row is not None
        assert row["id"] == draft_id
        assert row["kind"] == "reply"
        assert row["generated_by"] == "human"
        assert row["status"] == "pending_review"
        assert row["to_addrs"] == "a@b.com"
        assert row["subject"] == "Hi"
        assert row["body_text"] == "Body"
        assert row["account"] is None
        assert row["cc_addrs"] is None
        assert row["scheduled_at"] is None
        assert row["gmail_message_id"] is None
        assert row["sent_at"] is None

    def test_create_draft_with_all_fields(self, db):
        draft_id = create_draft(
            db,
            account="me@example.com",
            kind="compose",
            in_reply_to_gmail_id="g123",
            thread_id="t123",
            to_addrs="a@b.com",
            cc_addrs="c@b.com",
            subject="Subj",
            body_text="Body",
            generated_by="llm",
            scheduled_at=5000,
        )
        row = get_draft(db, draft_id)
        assert row["account"] == "me@example.com"
        assert row["kind"] == "compose"
        assert row["in_reply_to_gmail_id"] == "g123"
        assert row["thread_id"] == "t123"
        assert row["cc_addrs"] == "c@b.com"
        assert row["generated_by"] == "llm"
        assert row["scheduled_at"] == 5000


class TestUpdateDraftStatus:
    def test_update_status_returns_true_on_success(self, db):
        draft_id = create_draft(db, to_addrs="a@b.com", subject="s", body_text="b")
        assert update_draft_status(db, draft_id, "approved") is True
        row = get_draft(db, draft_id)
        assert row["status"] == "approved"
        assert row["updated_at"] is not None

    def test_update_status_returns_false_when_missing(self, db):
        assert update_draft_status(db, 999, "approved") is False

    def test_update_status_sets_gmail_message_id_and_sent_at(self, db):
        draft_id = create_draft(db, to_addrs="a@b.com", subject="s", body_text="b")
        ok = update_draft_status(
            db, draft_id, "sent", gmail_message_id="gm-1", sent_at=1234
        )
        assert ok is True
        row = get_draft(db, draft_id)
        assert row["status"] == "sent"
        assert row["gmail_message_id"] == "gm-1"
        assert row["sent_at"] == 1234

    def test_update_status_without_optional_args_leaves_them_unset(self, db):
        draft_id = create_draft(db, to_addrs="a@b.com", subject="s", body_text="b")
        update_draft_status(db, draft_id, "discarded")
        row = get_draft(db, draft_id)
        assert row["gmail_message_id"] is None
        assert row["sent_at"] is None


class TestGetDueScheduledDrafts:
    def test_excludes_non_approved(self, db):
        create_draft(
            db, to_addrs="a@b.com", subject="s", body_text="b", scheduled_at=100
        )
        assert get_due_scheduled_drafts(db, now_ts=200) == []

    def test_excludes_null_scheduled_at(self, db):
        draft_id = create_draft(db, to_addrs="a@b.com", subject="s", body_text="b")
        update_draft_status(db, draft_id, "approved")
        assert get_due_scheduled_drafts(db, now_ts=200) == []

    def test_excludes_future_scheduled_at(self, db):
        draft_id = create_draft(
            db, to_addrs="a@b.com", subject="s", body_text="b", scheduled_at=500
        )
        update_draft_status(db, draft_id, "approved")
        assert get_due_scheduled_drafts(db, now_ts=100) == []

    def test_includes_due_approved_scheduled_draft(self, db):
        draft_id = create_draft(
            db, to_addrs="a@b.com", subject="s", body_text="b", scheduled_at=100
        )
        update_draft_status(db, draft_id, "approved")
        rows = get_due_scheduled_drafts(db, now_ts=200)
        assert [r["id"] for r in rows] == [draft_id]

    def test_default_now_ts_uses_current_time(self, db):
        draft_id = create_draft(
            db, to_addrs="a@b.com", subject="s", body_text="b", scheduled_at=1
        )
        update_draft_status(db, draft_id, "approved")
        rows = get_due_scheduled_drafts(db)
        assert [r["id"] for r in rows] == [draft_id]


class TestSnoozeQueueItem:
    def test_snooze_sets_status_and_timestamp(self, db):
        qid = _seed_queue_item(db, "fp1")
        assert snooze_queue_item(db, qid, 5000) is True
        row = db.execute_sql(
            "SELECT status, snoozed_until FROM action_queue WHERE id = ?", (qid,)
        ).fetchone()
        assert row["status"] == "snoozed"
        assert row["snoozed_until"] == 5000

    def test_snooze_returns_false_when_missing(self, db):
        assert snooze_queue_item(db, 999, 5000) is False


class TestGetDueSnoozedItems:
    def test_excludes_non_snoozed(self, db):
        qid = _seed_queue_item(db, "fp1")
        assert get_due_snoozed_items(db, now_ts=100) == []

    def test_excludes_future_snoozed_until(self, db):
        qid = _seed_queue_item(db, "fp1")
        snooze_queue_item(db, qid, 500)
        assert get_due_snoozed_items(db, now_ts=100) == []

    def test_includes_due_snoozed_item(self, db):
        qid = _seed_queue_item(db, "fp1")
        snooze_queue_item(db, qid, 100)
        rows = get_due_snoozed_items(db, now_ts=200)
        assert [r["id"] for r in rows] == [qid]

    def test_default_now_ts_uses_current_time(self, db):
        qid = _seed_queue_item(db, "fp1")
        snooze_queue_item(db, qid, 1)
        rows = get_due_snoozed_items(db)
        assert [r["id"] for r in rows] == [qid]


class TestUnsnoozeQueueItem:
    def test_unsnooze_resets_status_and_clears_timestamp(self, db):
        qid = _seed_queue_item(db, "fp1")
        snooze_queue_item(db, qid, 5000)
        assert unsnooze_queue_item(db, qid) is True
        row = db.execute_sql(
            "SELECT status, snoozed_until FROM action_queue WHERE id = ?", (qid,)
        ).fetchone()
        assert row["status"] == "pending"
        assert row["snoozed_until"] is None

    def test_unsnooze_returns_false_when_missing(self, db):
        assert unsnooze_queue_item(db, 999) is False

    def test_unsnooze_then_not_returned_by_due_snoozed(self, db):
        qid = _seed_queue_item(db, "fp1")
        snooze_queue_item(db, qid, 1)
        unsnooze_queue_item(db, qid)
        assert get_due_snoozed_items(db) == []
