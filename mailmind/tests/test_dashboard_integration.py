"""Integration tests: render each dashboard tab against a REAL seeded SQLite DB.

These are deliberately NOT mocked at the query layer. The unit AppTests in
test_dashboard_app.py mock get_pending_queue_enriched / analytics_* etc., which
is why three real-runtime dashboard bugs slipped through this session:
  - render_insights_tab undefined (NameError)
  - ternary expression-statements magic-dumping a DeltaGenerator
  - get_pending_queue_enriched(limit=None) -> SQLite "datatype mismatch"

Here get_db() is pointed at a real, migrated, seeded Database, so every tab
runs its actual SQL and real Streamlit rendering. A tab that raises, or that
dumps a DeltaGenerator help table via magic, fails the test.
"""
from __future__ import annotations

import contextlib
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction, QueueItem
from mailmind.storage.queries import upsert_queue_item, update_sender_profile
from mailmind.utils.fingerprint import make_action_fingerprint

ACCT = "dudas.adam@cserkesz.hu"


@pytest.fixture(autouse=True)
def _clear_caches():
    for c in (st.cache_data, st.cache_resource):
        try:
            c.clear()
        except Exception:
            pass
    yield
    for c in (st.cache_data, st.cache_resource):
        try:
            c.clear()
        except Exception:
            pass


@pytest.fixture
def seeded_db():
    """A real migrated DB with enough data to exercise every tab."""
    tmp = tempfile.TemporaryDirectory()
    db = Database(Path(tmp.name) / "dash.db")
    now = int(time.time())

    # --- A reply-needed work email from a known (trusted-ish) sender ---
    db.insert_email(Email(gmail_id="e1", sender="alice@example.com",
                          subject="Project update", body_text="Please review by Friday.",
                          account=ACCT, date_ts=now))
    p1 = Prediction(email_gmail_id="e1", model="rules", labels=["WORK"],
                    priority_score=85, primary_label="WORK", confidence=0.9,
                    scoring_breakdown=json.dumps({"total_score": 85, "base_score": 60}),
                    account=ACCT)
    p1.channel = "team"
    p1.id = db.save_prediction(p1)
    upsert_queue_item(db, QueueItem(
        email_gmail_id="e1", prediction_id=p1.id, action="star",
        action_fingerprint=make_action_fingerprint("e1", "star", {}),
        status="pending", confidence=0.9, priority_score=85, account=ACCT,
        reason_json={"reply_needed": True, "thread_summary": "Waiting on sign-off",
                     "action_items": ["Please review"], "deadlines": ["Friday"],
                     "rule_matches": [], "trust_tier": "neutral",
                     "primary_label": "WORK", "score": 85,
                     "similar_past_actions": []},
    ))
    update_sender_profile(db, "alice@example.com", "approved")  # gives alice a profile

    # --- A newsletter from a brand-new sender (drives new-sender screening) ---
    db.insert_email(Email(gmail_id="e2", sender="news@bob.com",
                          subject="Heti hírlevél", body_text="leiratkozás",
                          account=ACCT, date_ts=now))
    p2 = Prediction(email_gmail_id="e2", model="rules", labels=["NEWSLETTER"],
                    priority_score=20, primary_label="NEWSLETTER", confidence=0.8,
                    scoring_breakdown=json.dumps({"total_score": 20}), account=ACCT)
    p2.channel = "newsletter"
    p2.id = db.save_prediction(p2)
    upsert_queue_item(db, QueueItem(
        email_gmail_id="e2", prediction_id=p2.id, action="archive",
        action_fingerprint=make_action_fingerprint("e2", "archive", {}),
        status="pending", confidence=0.8, priority_score=20, account=ACCT,
        reason_json={"reply_needed": False, "primary_label": "NEWSLETTER"},
    ))

    # --- A reviewed item so analytics_decision_times has data ---
    db.insert_email(Email(gmail_id="e3", sender="carol@example.com",
                          subject="Done", body_text="ok", account=ACCT, date_ts=now))
    p3 = Prediction(email_gmail_id="e3", model="rules", labels=["WORK"],
                    priority_score=70, primary_label="WORK", confidence=0.7,
                    scoring_breakdown=json.dumps({"total_score": 70}), account=ACCT)
    p3.channel = "personal"
    p3.id = db.save_prediction(p3)
    q3 = upsert_queue_item(db, QueueItem(
        email_gmail_id="e3", prediction_id=p3.id, action="label",
        action_fingerprint=make_action_fingerprint("e3", "label", {}),
        status="pending", confidence=0.7, priority_score=70, account=ACCT,
        reason_json={},
    ))
    db.execute_sql(
        "UPDATE action_queue SET status='approved', reviewed_at=created_at+120 WHERE id=?",
        (q3.id,))
    db._conn.commit()

    db.set_state("last_heartbeat_ts", str(now))
    yield db
    db.close()
    tmp.cleanup()


# --- AppTest wrappers (import inside so the temp script has the module) -------

def _now():
    from mailmind.dashboard import app as a
    a.render_now_tab("dudas.adam@cserkesz.hu")

def _review():
    from mailmind.dashboard import app as a
    a.render_review_tab("dudas.adam@cserkesz.hu")

def _insights():
    from mailmind.dashboard import app as a
    a.render_insights_tab("dudas.adam@cserkesz.hu")

def _automate():
    from mailmind.dashboard import app as a
    a.render_automate_tab("dudas.adam@cserkesz.hu")


@contextlib.contextmanager
def _real_db(db):
    """Point the dashboard's get_db at the seeded real DB (no query mocks)."""
    with patch("mailmind.dashboard.app.get_db", return_value=db):
        yield


def _render(fn, db):
    with _real_db(db):
        at = AppTest.from_function(fn)
        at.run()
    return at


def _assert_clean(at):
    assert not at.exception, f"tab raised: {at.exception}"
    blob = " ".join(el.value for el in at.markdown)
    # Magic-dumped DeltaGenerator help tables contain this; real content never does.
    assert "DeltaGenerator" not in blob, "a DeltaGenerator was rendered (magic dump)"


# --- The tests ----------------------------------------------------------------

def test_now_tab_real_db(seeded_db):
    at = _render(_now, seeded_db)
    _assert_clean(at)
    blob = " ".join(el.value for el in at.markdown)
    assert "Project update" in blob          # the reply-needed item rendered

def test_review_tab_real_db(seeded_db):
    # This is the exact path that crashed: _c_pending(None, account) -> limit=None.
    at = _render(_review, seeded_db)
    _assert_clean(at)

def test_insights_tab_real_db(seeded_db):
    # Runs all five real analytics aggregations + chart builders.
    at = _render(_insights, seeded_db)
    _assert_clean(at)

def test_automate_tab_real_db(seeded_db):
    at = _render(_automate, seeded_db)
    _assert_clean(at)

def test_all_tabs_on_empty_db():
    """Every tab must render on a fresh, empty (but migrated) DB too."""
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "empty.db")
        db.set_state("last_heartbeat_ts", str(int(time.time())))
        for fn in (_now, _review, _insights, _automate):
            at = _render(fn, db)
            _assert_clean(at)
        db.close()
