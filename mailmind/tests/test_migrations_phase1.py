"""Tests for Phase 1 migrations 0027-0029 (content/threading columns,
attachments table, account column on sender/label tables), plus Phase 3+
migrations 0030-0031 (drafts table, action_queue snooze column).
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from mailmind.storage.database import Database
from mailmind.storage.migrations import apply_migrations


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        database = Database(pathlib.Path(d) / "t.db")
        yield database
        database.close()


def _columns(db: Database, table: str) -> set[str]:
    return {r[1] for r in db._conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_0027_emails_columns_added(db):
    cols = _columns(db, "emails")
    for col in ("body_html", "message_id", "in_reply_to", "references_header", "history_id"):
        assert col in cols, f"missing emails.{col}"


def test_0027_message_id_index_exists(db):
    names = {r[0] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_emails_message_id" in names


def test_0028_attachments_table_and_unique_index(db):
    cols = _columns(db, "attachments")
    for col in ("id", "email_gmail_id", "account", "gmail_attachment_id",
                "filename", "mime_type", "size_bytes", "created_at"):
        assert col in cols, f"missing attachments.{col}"

    names = {r[0] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_attachments_email_attachment" in names

    # Unique index enforces no duplicate (email_gmail_id, gmail_attachment_id).
    db._conn.execute(
        "INSERT INTO attachments (email_gmail_id, gmail_attachment_id, filename)"
        " VALUES ('e1', 'a1', 'foo.pdf')"
    )
    db._conn.commit()
    with pytest.raises(Exception):
        db._conn.execute(
            "INSERT INTO attachments (email_gmail_id, gmail_attachment_id, filename)"
            " VALUES ('e1', 'a1', 'dup.pdf')"
        )


def test_0029_account_column_added_to_sender_and_label_tables(db):
    for table in ("sender_profiles", "sender_reputation", "label_priority", "user_corrections"):
        assert "account" in _columns(db, table), f"missing {table}.account"


def test_0029_backfill_helper_stamps_primary_account(db, monkeypatch):
    """`_apply_sender_and_label_account_dimension` backfills NULL accounts.

    Migration 0029 already ran (via Database's constructor), so a fresh row
    inserted afterwards has account=NULL (only pre-existing rows are
    backfilled at migration time) — this calls the helper directly, the same
    way 0015's `_apply_account_dimension` is exercised, to prove the backfill
    logic itself works against `_primary_account()`.
    """
    from mailmind.storage.migrations import _apply_sender_and_label_account_dimension

    db._conn.execute(
        "INSERT INTO sender_profiles (sender_email, total_seen) VALUES ('a@b.com', 1)"
    )
    db._conn.commit()
    monkeypatch.setenv("MAILMIND_USER_EMAIL", "primary@example.com")
    monkeypatch.delenv("MAILMIND_ACCOUNTS", raising=False)
    _apply_sender_and_label_account_dimension(db._conn)
    db._conn.commit()
    row = db._conn.execute(
        "SELECT account FROM sender_profiles WHERE sender_email = 'a@b.com'"
    ).fetchone()
    assert row["account"] == "primary@example.com"


def test_migrations_idempotent_full_suite(db):
    """Running apply_migrations() twice must be a no-op: no error, no dupes."""
    apply_migrations(db._conn)
    apply_migrations(db._conn)
    # Sanity: schema still intact and queryable after the extra re-application.
    cur = db._conn.execute("SELECT COUNT(*) FROM attachments")
    assert cur.fetchone()[0] == 0


def test_0030_drafts_table_columns(db):
    cols = _columns(db, "drafts")
    for col in (
        "id", "account", "kind", "in_reply_to_gmail_id", "thread_id",
        "to_addrs", "cc_addrs", "subject", "body_text", "generated_by",
        "status", "scheduled_at", "gmail_message_id", "created_at",
        "updated_at", "sent_at",
    ):
        assert col in cols, f"missing drafts.{col}"


def test_0030_drafts_indexes_exist(db):
    names = {r[0] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_drafts_status" in names
    assert "idx_drafts_account" in names


def test_0030_drafts_defaults(db):
    db._conn.execute(
        "INSERT INTO drafts (to_addrs, subject, body_text) VALUES ('a@b.com', 'hi', 'body')"
    )
    db._conn.commit()
    row = db._conn.execute("SELECT * FROM drafts WHERE to_addrs = 'a@b.com'").fetchone()
    assert row["kind"] == "reply"
    assert row["generated_by"] == "human"
    assert row["status"] == "pending_review"


def test_0031_action_queue_snoozed_until_column_added(db):
    assert "snoozed_until" in _columns(db, "action_queue")


def test_0031_snoozed_until_nullable_and_settable(db):
    db._conn.execute(
        "INSERT INTO action_queue (email_gmail_id, action, action_fingerprint) "
        "VALUES ('e1', 'archive', 'fp1')"
    )
    db._conn.commit()
    row = db._conn.execute(
        "SELECT snoozed_until FROM action_queue WHERE action_fingerprint = 'fp1'"
    ).fetchone()
    assert row["snoozed_until"] is None

    db._conn.execute(
        "UPDATE action_queue SET status = 'snoozed', snoozed_until = 12345"
        " WHERE action_fingerprint = 'fp1'"
    )
    db._conn.commit()
    row = db._conn.execute(
        "SELECT status, snoozed_until FROM action_queue WHERE action_fingerprint = 'fp1'"
    ).fetchone()
    assert row["status"] == "snoozed"
    assert row["snoozed_until"] == 12345


def test_migrations_0030_0031_idempotent(db):
    """Re-running apply_migrations() after 0030/0031 already applied is a no-op."""
    apply_migrations(db._conn)
    apply_migrations(db._conn)
    cols = _columns(db, "drafts")
    assert "id" in cols
    assert "snoozed_until" in _columns(db, "action_queue")
    # No duplicate index/table errors, and the table is still empty/queryable.
    cur = db._conn.execute("SELECT COUNT(*) FROM drafts")
    assert cur.fetchone()[0] == 0
