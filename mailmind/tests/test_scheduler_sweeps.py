"""Tests for the Phase 4 scheduler watch-loop sweeps: _maybe_unsnooze and
_maybe_send_scheduled_drafts (mailmind/main.py).

Follows this codebase's established _maybe_* testing convention (see
test_auto_refresh_labels.py): a real tempfile-backed Database, monkeypatch for
credential/executor construction, direct calls to the sweep functions.
"""
from __future__ import annotations

import pathlib
import tempfile
import time
from unittest.mock import MagicMock

import mailmind.main as main_mod
from mailmind.main import _maybe_send_scheduled_drafts, _maybe_unsnooze
from mailmind.storage.database import Database
from mailmind.storage.models import Email
from mailmind.storage.queries import (
    create_draft,
    get_draft,
    snooze_queue_item,
    update_draft_status,
)


def _db():
    return Database(pathlib.Path(tempfile.mkdtemp()) / "t.db")


def _seed_email(db, gmail_id="g1", account=None):
    db.insert_email(Email(
        gmail_id=gmail_id, sender="alice@example.com", subject="s", snippet="x",
        body_text="b", recipients=["me@example.com"], date_ts=1, labels=[],
        parsed=True, account=account,
    ))


def _seed_pending_queue_item(db, gmail_id="g1", account=None) -> int:
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO action_queue
                (email_gmail_id, account, action, params_json, action_fingerprint,
                 status, confidence, priority_score, reason_json, created_at, updated_at)
            VALUES (?, ?, 'label', '{}', ?, 'pending', 0.7, 50, '{}', ?, ?)
            """,
            (gmail_id, account, f"fp_{gmail_id}", now, now),
        )
        return cur.lastrowid


class TestMaybeUnsnooze:
    def test_respects_interval(self):
        db = _db()
        _maybe_unsnooze(db, interval_seconds=86400)  # first run: fires
        row = db.execute_sql(
            "SELECT value FROM system_state WHERE key='last_unsnooze_ts'"
        ).fetchone()
        assert row is not None
        first_ts = row["value"]

        _maybe_unsnooze(db, interval_seconds=86400)  # within interval: skip
        row2 = db.execute_sql(
            "SELECT value FROM system_state WHERE key='last_unsnooze_ts'"
        ).fetchone()
        assert row2["value"] == first_ts  # unchanged — confirms the skip

    def test_calling_twice_quickly_only_processes_once(self, monkeypatch):
        db = _db()
        calls = {"n": 0}

        def _fake_get_due(*a, **k):
            calls["n"] += 1
            return []

        monkeypatch.setattr(
            "mailmind.storage.queries.get_due_snoozed_items", _fake_get_due,
        )
        _maybe_unsnooze(db, interval_seconds=300)
        _maybe_unsnooze(db, interval_seconds=300)
        assert calls["n"] == 1

    def test_due_snoozed_item_gets_unsnoozed(self):
        db = _db()
        _seed_email(db, gmail_id="g1")
        queue_id = _seed_pending_queue_item(db, gmail_id="g1")
        past = int(time.time()) - 10
        assert snooze_queue_item(db, queue_id, past)

        row = db.execute_sql(
            "SELECT status FROM action_queue WHERE id=?", (queue_id,)
        ).fetchone()
        assert row["status"] == "snoozed"

        _maybe_unsnooze(db, interval_seconds=0)

        row2 = db.execute_sql(
            "SELECT status, snoozed_until FROM action_queue WHERE id=?", (queue_id,)
        ).fetchone()
        assert row2["status"] == "pending"

    def test_not_yet_due_snoozed_item_stays_snoozed(self):
        db = _db()
        _seed_email(db, gmail_id="g1")
        queue_id = _seed_pending_queue_item(db, gmail_id="g1")
        future = int(time.time()) + 3600
        snooze_queue_item(db, queue_id, future)

        _maybe_unsnooze(db, interval_seconds=0)

        row = db.execute_sql(
            "SELECT status FROM action_queue WHERE id=?", (queue_id,)
        ).fetchone()
        assert row["status"] == "snoozed"


class TestMaybeSendScheduledDrafts:
    def test_respects_interval(self, monkeypatch):
        db = _db()
        calls = {"n": 0}
        monkeypatch.setattr(
            "mailmind.storage.queries.get_due_scheduled_drafts",
            lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), [])[1],
        )
        _maybe_send_scheduled_drafts(db, interval_seconds=300)
        _maybe_send_scheduled_drafts(db, interval_seconds=300)
        assert calls["n"] == 1

    def test_due_draft_calls_handle_approve_and_send_with_account_executor(self, monkeypatch):
        db = _db()
        _seed_email(db, gmail_id="orig1", account="acct@example.com")
        draft_id = create_draft(
            db, account="acct@example.com", kind="reply",
            in_reply_to_gmail_id="orig1", to_addrs="bob@example.com",
            subject="Re: hi", body_text="Sure thing.",
            scheduled_at=int(time.time()) - 10,
        )
        update_draft_status(db, draft_id, "approved")

        mock_creds = MagicMock()
        mock_service = MagicMock()
        monkeypatch.setattr(main_mod, "load_stored_credentials", lambda account: mock_creds)
        monkeypatch.setattr(main_mod, "build_gmail_service", lambda creds: mock_service)

        sent_calls = []

        def _fake_handle_approve_and_send(db_arg, draft_id_arg, executor_arg):
            sent_calls.append((draft_id_arg, executor_arg))
            return True

        monkeypatch.setattr(
            "mailmind.intelligence.feedback.handle_approve_and_send",
            _fake_handle_approve_and_send,
        )

        _maybe_send_scheduled_drafts(db, interval_seconds=0)

        assert len(sent_calls) == 1
        sent_draft_id, executor = sent_calls[0]
        assert sent_draft_id == draft_id
        # A real ActionExecutor was constructed from the mocked per-account
        # credentials/service — not a generic/default one.
        assert executor.service is mock_service

    def test_not_yet_due_draft_is_skipped(self, monkeypatch):
        db = _db()
        _seed_email(db, gmail_id="orig1", account="acct@example.com")
        draft_id = create_draft(
            db, account="acct@example.com", kind="reply",
            in_reply_to_gmail_id="orig1", to_addrs="bob@example.com",
            subject="Re: hi", body_text="Later.",
            scheduled_at=int(time.time()) + 3600,
        )
        update_draft_status(db, draft_id, "approved")

        sent_calls = []
        monkeypatch.setattr(
            "mailmind.intelligence.feedback.handle_approve_and_send",
            lambda *a, **k: sent_calls.append(a),
        )
        _maybe_send_scheduled_drafts(db, interval_seconds=0)
        assert sent_calls == []

    def test_account_with_no_credentials_is_skipped_not_crashed(self, monkeypatch):
        db = _db()
        _seed_email(db, gmail_id="orig1", account="no_creds@example.com")
        draft_id = create_draft(
            db, account="no_creds@example.com", kind="reply",
            in_reply_to_gmail_id="orig1", to_addrs="bob@example.com",
            subject="Re: hi", body_text="Sure.",
            scheduled_at=int(time.time()) - 10,
        )
        update_draft_status(db, draft_id, "approved")

        monkeypatch.setattr(main_mod, "load_stored_credentials", lambda account: None)

        sent_calls = []
        monkeypatch.setattr(
            "mailmind.intelligence.feedback.handle_approve_and_send",
            lambda *a, **k: sent_calls.append(a),
        )

        # Must not raise even though this account has no usable credentials.
        _maybe_send_scheduled_drafts(db, interval_seconds=0)
        assert sent_calls == []

        row = get_draft(db, draft_id)
        # Skipped, not marked send_failed — it stays 'approved' and will be
        # retried on a later cycle once credentials exist.
        assert row["status"] == "approved"
