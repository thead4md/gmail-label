"""Pass 11B: analytics query shapes."""

from __future__ import annotations

import tempfile
import pathlib
import time
import pytest

from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction, QueueItem
from mailmind.storage.queries import (
    analytics_label_distribution,
    analytics_channel_distribution,
    analytics_top_senders,
    analytics_decision_times,
    analytics_channel_weekday,
    upsert_queue_item,
)
from mailmind.utils.fingerprint import make_action_fingerprint


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        database = Database(pathlib.Path(d) / "t.db")
        yield database
        database.close()


def _seed(db):
    now = int(time.time())
    for i, (lbl, ch) in enumerate([("WORK", "team"), ("WORK", "team"),
                                   ("NEWSLETTER", "newsletter")]):
        gid = f"e{i}"
        db.insert_email(Email(gmail_id=gid, sender=f"s{i}@x.com", subject="S"))
        p = Prediction(email_gmail_id=gid, model="rules", labels=[lbl],
                       priority_score=70, primary_label=lbl)
        p.channel = ch
        db.save_prediction(p)
    return now


def test_label_distribution(db):
    since = _seed(db) - 86400
    rows = analytics_label_distribution(db, since)
    d = {r["label"]: r["count"] for r in rows}
    assert d.get("WORK") == 2 and d.get("NEWSLETTER") == 1


def test_channel_distribution(db):
    since = _seed(db) - 86400
    rows = analytics_channel_distribution(db, since)
    d = {r["channel"]: r["count"] for r in rows}
    assert d.get("team") == 2 and d.get("newsletter") == 1


def test_channel_weekday_shape(db):
    since = _seed(db) - 86400
    rows = analytics_channel_weekday(db, since)
    assert all({"channel", "weekday", "count"} <= set(r) for r in rows)


def test_top_senders_and_decision_times(db):
    now = int(time.time())
    db.insert_email(Email(gmail_id="q1", sender="boss@x.com", subject="S"))
    fp = make_action_fingerprint("q1", "star", {})
    q = upsert_queue_item(db, QueueItem(email_gmail_id="q1", action="star",
                                        action_fingerprint=fp, status="pending"))
    db.execute_sql("UPDATE action_queue SET reviewed_at = created_at + 120 WHERE id = ?",
                   (q.id,))
    db._conn.commit()
    since = now - 86400
    senders = analytics_top_senders(db, since)
    assert any(s["sender"] == "boss@x.com" for s in senders)
    times = analytics_decision_times(db, since)
    assert any(abs(t["minutes"] - 2.0) < 0.1 for t in times)


def test_empty_window_returns_empty(db):
    future = int(time.time()) + 999999
    assert analytics_label_distribution(db, future) == []
