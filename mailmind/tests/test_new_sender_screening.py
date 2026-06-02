"""Pass 10E: new-sender screening."""
from __future__ import annotations

import tempfile
import pathlib
import pytest

from mailmind.storage.database import Database
from mailmind.storage.models import Email, QueueItem
from mailmind.storage.queries import (
    get_new_senders, set_sender_trust_tier, upsert_queue_item,
    update_sender_profile, get_sender_profiles,
)
from mailmind.intelligence.feedback import (
    handle_know_sender, handle_mute_sender, handle_block_sender,
)
from mailmind.utils.fingerprint import make_action_fingerprint


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        database = Database(pathlib.Path(d) / "t.db")
        yield database
        database.close()


def _queue(db, gmail_id, sender, action="star"):
    db.insert_email(Email(gmail_id=gmail_id, sender=sender, subject="S"))
    fp = make_action_fingerprint(gmail_id, action, {})
    return upsert_queue_item(db, QueueItem(
        email_gmail_id=gmail_id, action=action, action_fingerprint=fp, status="pending"))


def test_new_sender_with_no_profile_listed(db):
    _queue(db, "n1", "fresh@new.com")
    senders = [s["sender"] for s in get_new_senders(db)]
    assert "fresh@new.com" in senders


def test_sender_with_decisions_not_listed(db):
    q = _queue(db, "n2", "known@x.com")
    update_sender_profile(db, "known@x.com", "approved")
    senders = [s["sender"] for s in get_new_senders(db)]
    assert "known@x.com" not in senders


def test_know_sender_sets_trusted(db):
    _queue(db, "n3", "buddy@x.com")
    assert handle_know_sender(db, "buddy@x.com") is True
    prof = next(p for p in get_sender_profiles(db) if p["sender_email"] == "buddy@x.com")
    assert prof["trust_tier"] == "trusted"


def test_mute_sender_sets_watchlist(db):
    _queue(db, "n4", "spam@x.com")
    assert handle_mute_sender(db, "spam@x.com") is True
    prof = next(p for p in get_sender_profiles(db) if p["sender_email"] == "spam@x.com")
    assert prof["trust_tier"] == "watchlist"


def test_block_sender_rejects_pending(db):
    _queue(db, "n5", "bad@x.com")
    assert handle_block_sender(db, "bad@x.com") is True
    row = db.execute_sql(
        "SELECT status FROM action_queue WHERE email_gmail_id = ?", ("n5",)).fetchone()
    assert row["status"] == "rejected"


def test_set_trust_tier_rejects_invalid(db):
    with pytest.raises(ValueError):
        set_sender_trust_tier(db, "a@b.com", "bogus")


def test_handlers_false_on_empty_sender(db):
    assert handle_know_sender(db, "") is False
    assert handle_block_sender(db, "") is False
