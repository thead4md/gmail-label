"""Tests for the Phase 1 inbox query contract: get_all_emails, search_emails,
get_thread_emails. Three other agents build UI tabs against these exact
signatures, so these tests pin down filtering/ordering/pagination behavior.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction
from mailmind.storage.queries import get_all_emails, search_emails, get_thread_emails


@pytest.fixture
def db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    database = Database(db_path)
    yield database
    database.close()
    db_path.unlink(missing_ok=True)


def _seed_email(db: Database, gmail_id, thread_id, sender, subject, snippet,
                 date_ts, account=None, body_text=None, labels=None, user_labels=None):
    email = Email(
        gmail_id=gmail_id, thread_id=thread_id, sender=sender, subject=subject,
        snippet=snippet, date_ts=date_ts, account=account, body_text=body_text,
        labels=labels or [],
    )
    db.insert_email(email)
    if user_labels is not None:
        db.set_email_user_labels(gmail_id, user_labels)


def _seed_prediction(db: Database, gmail_id, primary_label, channel="human", confidence=0.8):
    db.save_prediction(Prediction(
        email_gmail_id=gmail_id, model="rules", labels=[primary_label],
        priority_score=50, primary_label=primary_label, channel=channel,
        confidence=confidence,
    ))


class TestGetAllEmails:
    def test_empty(self, db):
        assert get_all_emails(db) == []

    def test_returns_rows_ordered_by_date_desc(self, db):
        _seed_email(db, "e1", "t1", "a@x.com", "Older", "snip1", 100)
        _seed_email(db, "e2", "t2", "b@x.com", "Newer", "snip2", 200)
        rows = get_all_emails(db)
        assert [r["gmail_id"] for r in rows] == ["e2", "e1"]

    def test_row_shape_includes_expected_keys(self, db):
        _seed_email(db, "e1", "t1", "a@x.com", "Subj", "snip", 100)
        _seed_prediction(db, "e1", "WORK", channel="human", confidence=0.9)
        row = get_all_emails(db)[0]
        for key in ("gmail_id", "thread_id", "sender", "subject", "snippet",
                    "date_ts", "primary_label", "channel", "confidence"):
            assert key in row
        assert row["primary_label"] == "WORK"
        assert row["channel"] == "human"
        assert row["confidence"] == 0.9

    def test_left_join_email_without_prediction_has_nulls(self, db):
        _seed_email(db, "e1", "t1", "a@x.com", "Subj", "snip", 100)
        row = get_all_emails(db)[0]
        assert row["primary_label"] is None
        assert row["channel"] is None
        assert row["confidence"] is None

    def test_account_filter(self, db):
        _seed_email(db, "e1", "t1", "a@x.com", "Subj1", "s1", 100, account="acct1")
        _seed_email(db, "e2", "t2", "b@x.com", "Subj2", "s2", 200, account="acct2")
        rows = get_all_emails(db, account="acct1")
        assert [r["gmail_id"] for r in rows] == ["e1"]

    def test_folder_filter_matches_labels_csv(self, db):
        _seed_email(db, "e1", "t1", "a@x.com", "Subj1", "s1", 100, labels=["INBOX", "WORK"])
        _seed_email(db, "e2", "t2", "b@x.com", "Subj2", "s2", 200, labels=["SPAM"])
        rows = get_all_emails(db, folder="WORK")
        assert [r["gmail_id"] for r in rows] == ["e1"]

    def test_folder_filter_matches_user_labels(self, db):
        _seed_email(db, "e1", "t1", "a@x.com", "Subj1", "s1", 100)
        db.set_email_user_labels("e1", "Personal,Finance")
        _seed_email(db, "e2", "t2", "b@x.com", "Subj2", "s2", 200)
        db.set_email_user_labels("e2", "Work")
        rows = get_all_emails(db, folder="Finance")
        assert [r["gmail_id"] for r in rows] == ["e1"]

    def test_folder_filter_resolves_custom_label_name_to_gmail_label_id(self, db):
        # Real Gmail custom labels are mirrored onto emails.labels as opaque
        # ids (e.g. "Label_99"), never as their human display name — only
        # gmail_label_map knows "Label_99" means "Work". A folder lookup by
        # display name must resolve through that map, not substring-match
        # the name directly against the id column. Passing account= here
        # (as the dashboard always does) exercises the account-scoped
        # gmail_label_map lookup branch, not just the unscoped one.
        _seed_email(db, "e1", "t1", "a@x.com", "Subj1", "s1", 100,
                     account="acct1", labels=["INBOX", "Label_99"])
        _seed_email(db, "e2", "t2", "b@x.com", "Subj2", "s2", 200,
                     account="acct1", labels=["INBOX", "Label_50"])
        db.upsert_label_map("acct1", {"Label_99": "Work", "Label_50": "Finance"})
        rows = get_all_emails(db, account="acct1", folder="Work")
        assert [r["gmail_id"] for r in rows] == ["e1"]

    def test_pagination_limit_and_offset(self, db):
        for i in range(5):
            _seed_email(db, f"e{i}", f"t{i}", "a@x.com", f"Subj{i}", "s", 100 + i)
        rows = get_all_emails(db, limit=2, offset=0)
        assert [r["gmail_id"] for r in rows] == ["e4", "e3"]
        rows2 = get_all_emails(db, limit=2, offset=2)
        assert [r["gmail_id"] for r in rows2] == ["e2", "e1"]

    def test_search_param_matches_subject(self, db):
        _seed_email(db, "e1", "t1", "a@x.com", "Invoice due", "s", 100)
        _seed_email(db, "e2", "t2", "b@x.com", "Meeting notes", "s", 200)
        rows = get_all_emails(db, search="Invoice")
        assert [r["gmail_id"] for r in rows] == ["e1"]


class TestSearchEmails:
    def test_empty(self, db):
        assert search_emails(db, "anything") == []

    def test_matches_subject_sender_snippet_or_body(self, db):
        _seed_email(db, "e1", "t1", "alice@x.com", "Hello", "s1", 100, body_text="nothing relevant")
        _seed_email(db, "e2", "t2", "bob@x.com", "Other", "budget update", 200, body_text="irrelevant")
        _seed_email(db, "e3", "t3", "carol@x.com", "Unrelated", "s3", 300, body_text="contains budgetword here")
        rows = search_emails(db, "budget")
        ids = {r["gmail_id"] for r in rows}
        assert ids == {"e2", "e3"}

    def test_ordered_by_date_desc(self, db):
        _seed_email(db, "e1", "t1", "a@x.com", "match one", "s", 100)
        _seed_email(db, "e2", "t2", "a@x.com", "match two", "s", 300)
        _seed_email(db, "e3", "t3", "a@x.com", "match three", "s", 200)
        rows = search_emails(db, "match")
        assert [r["gmail_id"] for r in rows] == ["e2", "e3", "e1"]

    def test_account_filter(self, db):
        _seed_email(db, "e1", "t1", "a@x.com", "match", "s", 100, account="acct1")
        _seed_email(db, "e2", "t2", "a@x.com", "match", "s", 200, account="acct2")
        rows = search_emails(db, "match", account="acct1")
        assert [r["gmail_id"] for r in rows] == ["e1"]

    def test_pagination(self, db):
        for i in range(5):
            _seed_email(db, f"e{i}", f"t{i}", "a@x.com", "match", "s", 100 + i)
        rows = search_emails(db, "match", limit=2, offset=1)
        assert [r["gmail_id"] for r in rows] == ["e3", "e2"]

    def test_uses_parameterized_query_no_sql_injection(self, db):
        _seed_email(db, "e1", "t1", "a@x.com", "Safe subject", "s", 100)
        # A classic injection payload must be treated as a literal substring,
        # not executed as SQL — this should simply match nothing (and not error).
        rows = search_emails(db, "'; DROP TABLE emails; --")
        assert rows == []
        # Table must still exist and be queryable.
        assert search_emails(db, "Safe") != []


class TestGetThreadEmails:
    def test_empty(self, db):
        assert get_thread_emails(db, "nope") == []

    def test_returns_only_matching_thread_chronological(self, db):
        _seed_email(db, "e1", "thread-a", "a@x.com", "First", "s", 300)
        _seed_email(db, "e2", "thread-a", "b@x.com", "Reply", "s", 100)
        _seed_email(db, "e3", "thread-a", "a@x.com", "Reply 2", "s", 200)
        _seed_email(db, "e4", "thread-b", "c@x.com", "Other thread", "s", 50)
        rows = get_thread_emails(db, "thread-a")
        assert [r["gmail_id"] for r in rows] == ["e2", "e3", "e1"]

    def test_account_filter(self, db):
        _seed_email(db, "e1", "thread-a", "a@x.com", "First", "s", 100, account="acct1")
        _seed_email(db, "e2", "thread-a", "a@x.com", "Second", "s", 200, account="acct2")
        rows = get_thread_emails(db, "thread-a", account="acct1")
        assert [r["gmail_id"] for r in rows] == ["e1"]

    def test_row_includes_prediction_fields(self, db):
        _seed_email(db, "e1", "thread-a", "a@x.com", "First", "s", 100)
        _seed_prediction(db, "e1", "URGENT", channel="human", confidence=0.95)
        row = get_thread_emails(db, "thread-a")[0]
        assert row["primary_label"] == "URGENT"
        assert row["confidence"] == 0.95
