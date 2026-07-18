"""Tests for Phase 1 migrations 0027-0029 (content/threading columns,
attachments table, account column on sender/label tables).
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
