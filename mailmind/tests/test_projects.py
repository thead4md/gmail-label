"""Tests for thread -> project conversion (client-strategy reframe §4.5):
queries.create_project/get_project/get_projects/close_project and
intelligence/projects.py's promote_thread_to_project.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction
from mailmind.storage.queries import create_project, get_project, get_projects, close_project
from mailmind.intelligence.projects import promote_thread_to_project, _clean_title


@pytest.fixture
def db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    database = Database(db_path)
    yield database
    database.close()
    db_path.unlink(missing_ok=True)


def _seed_thread_email(db, gmail_id, thread_id, sender, recipients, subject, date_ts,
                       account=None, action_items=None, deadlines=None):
    db.insert_email(Email(
        gmail_id=gmail_id, thread_id=thread_id, sender=sender, recipients=recipients,
        subject=subject, snippet="s", date_ts=date_ts, account=account,
    ))
    ctx = json.dumps({
        "action_items": action_items or [], "deadlines": deadlines or [],
        "is_thread": True, "thread_length": 1, "reply_needed": False,
        "open_question_detected": False, "waiting_on_other_party": False,
    })
    db.save_prediction(Prediction(
        email_gmail_id=gmail_id, model="rules", labels=[], priority_score=50,
        thread_context_json=ctx, account=account,
    ))


# --------------------------------------------------------------------------- #
# _clean_title (pure)
# --------------------------------------------------------------------------- #
class TestCleanTitle:
    def test_strips_single_re_prefix(self):
        assert _clean_title("Re: Budget approval") == "Budget approval"

    def test_strips_stacked_prefixes(self):
        assert _clean_title("Re: Re: Fwd: Budget approval") == "Budget approval"

    def test_strips_hungarian_prefixes(self):
        assert _clean_title("Válasz: Továbbítás: Költségvetés") == "Költségvetés"

    def test_no_prefix_unchanged(self):
        assert _clean_title("Budget approval") == "Budget approval"

    def test_empty_subject_gets_placeholder(self):
        assert _clean_title(None) == "(untitled thread)"
        assert _clean_title("") == "(untitled thread)"


# --------------------------------------------------------------------------- #
# Query contract
# --------------------------------------------------------------------------- #
class TestProjectQueries:
    def test_create_and_get(self, db):
        pid = create_project(
            db, account="me", thread_id="t1", title="Budget",
            participants=[{"email": "bob@y.com", "name": "Bob"}],
            action_items=["Send the report"], deadline_ts=1000,
        )
        proj = get_project(db, pid)
        assert proj["title"] == "Budget"
        assert proj["participants"] == [{"email": "bob@y.com", "name": "Bob"}]
        assert proj["action_items"] == ["Send the report"]
        assert proj["deadline_ts"] == 1000
        assert proj["status"] == "active"

    def test_create_is_idempotent_per_thread(self, db):
        id1 = create_project(db, account="me", thread_id="t1", title="v1",
                              participants=[], action_items=[], deadline_ts=None)
        id2 = create_project(db, account="me", thread_id="t1", title="v2",
                              participants=[], action_items=[], deadline_ts=None)
        assert id1 == id2
        assert get_project(db, id1)["title"] == "v2"

    def test_idempotent_with_no_account_configured(self, db):
        # Same NULL-account uniqueness concern as loops -- must not duplicate.
        id1 = create_project(db, account=None, thread_id="t1", title="v1",
                              participants=[], action_items=[], deadline_ts=None)
        id2 = create_project(db, account=None, thread_id="t1", title="v2",
                              participants=[], action_items=[], deadline_ts=None)
        assert id1 == id2

    def test_get_project_missing_returns_none(self, db):
        assert get_project(db, 9999) is None

    def test_get_projects_filters_by_status(self, db):
        pid1 = create_project(db, account="me", thread_id="t1", title="active one",
                               participants=[], action_items=[], deadline_ts=None)
        pid2 = create_project(db, account="me", thread_id="t2", title="done one",
                               participants=[], action_items=[], deadline_ts=None)
        close_project(db, pid2)
        active = get_projects(db, account="me", status="active")
        assert [p["id"] for p in active] == [pid1]
        done = get_projects(db, account="me", status="done")
        assert [p["id"] for p in done] == [pid2]
        everything = get_projects(db, account="me", status=None)
        assert len(everything) == 2

    def test_close_project(self, db):
        pid = create_project(db, account="me", thread_id="t1", title="x",
                              participants=[], action_items=[], deadline_ts=None)
        assert close_project(db, pid) is True
        assert get_project(db, pid)["status"] == "done"
        assert close_project(db, pid) is False  # already closed


# --------------------------------------------------------------------------- #
# promote_thread_to_project (integration)
# --------------------------------------------------------------------------- #
class TestPromoteThreadToProject:
    def test_raises_for_unknown_thread(self, db):
        with pytest.raises(ValueError):
            promote_thread_to_project(db, "no-such-thread")

    def test_title_from_oldest_message_reply_prefix_stripped(self, db):
        _seed_thread_email(db, "m1", "t1", "bob@y.com", ["me@x.com"], "Budget approval", date_ts=100)
        _seed_thread_email(db, "m2", "t1", "me@x.com", ["bob@y.com"], "Re: Budget approval", date_ts=200)
        pid = promote_thread_to_project(db, "t1")
        proj = get_project(db, pid)
        assert proj["title"] == "Budget approval"

    def test_participants_union_across_messages(self, db):
        _seed_thread_email(db, "m1", "t1", "Bob Smith <bob@y.com>", ["me@x.com"], "s", date_ts=100)
        _seed_thread_email(db, "m2", "t1", "me@x.com", ["bob@y.com", "carol@y.com"], "s", date_ts=200)
        pid = promote_thread_to_project(db, "t1")
        proj = get_project(db, pid)
        emails = {p["email"] for p in proj["participants"]}
        assert emails == {"bob@y.com", "me@x.com", "carol@y.com"}
        bob = next(p for p in proj["participants"] if p["email"] == "bob@y.com")
        assert bob["name"] == "Bob Smith"

    def test_action_items_and_deadlines_unioned_and_deduped(self, db):
        _seed_thread_email(db, "m1", "t1", "bob@y.com", ["me@x.com"], "s", date_ts=100,
                           action_items=["Send the doc"], deadlines=["by Friday"])
        _seed_thread_email(db, "m2", "t1", "me@x.com", ["bob@y.com"], "s", date_ts=200,
                           action_items=["Send the doc", "Review comments"], deadlines=[])
        pid = promote_thread_to_project(db, "t1")
        proj = get_project(db, pid)
        assert proj["action_items"] == ["Send the doc", "Review comments"]

    def test_deadline_resolved_to_earliest_parseable_timestamp(self, db):
        _seed_thread_email(db, "m1", "t1", "bob@y.com", ["me@x.com"], "s", date_ts=100,
                           deadlines=["ASAP", "tomorrow"])
        pid = promote_thread_to_project(db, "t1")
        proj = get_project(db, pid)
        assert proj["deadline_ts"] is not None  # "ASAP" unparseable, "tomorrow" resolves

    def test_promoting_same_thread_twice_is_idempotent(self, db):
        _seed_thread_email(db, "m1", "t1", "bob@y.com", ["me@x.com"], "s", date_ts=100)
        pid1 = promote_thread_to_project(db, "t1")
        pid2 = promote_thread_to_project(db, "t1")
        assert pid1 == pid2

    def test_account_filter(self, db):
        _seed_thread_email(db, "m1", "t1", "bob@y.com", ["me@x.com"], "s", date_ts=100, account="acct1")
        _seed_thread_email(db, "m2", "t1", "carol@y.com", ["me@x.com"], "s", date_ts=200, account="acct2")
        pid = promote_thread_to_project(db, "t1", account="acct1")
        proj = get_project(db, pid)
        emails = {p["email"] for p in proj["participants"]}
        assert "carol@y.com" not in emails
