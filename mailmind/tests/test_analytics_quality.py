"""Track B: per-tier correction rate, autopilot precision, and LLM cost queries."""
from __future__ import annotations

import json
import tempfile
import pathlib

import pytest

from mailmind.storage.database import Database
from mailmind.storage.queries import (
    record_llm_usage,
    analytics_llm_cost,
    analytics_tier_quality,
    analytics_autopilot_precision,
)


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        yield Database(pathlib.Path(d) / "t.db")


def _email(db, gid, sender="a@b.com"):
    with db.transaction() as cur:
        cur.execute("INSERT INTO emails (gmail_id, sender) VALUES (?, ?)", (gid, sender))


def _prediction(db, gid, source, label, ts=1000):
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO predictions (email_gmail_id, model, classifier_source, "
            "primary_label, created_at) VALUES (?, 'm', ?, ?, ?)",
            (gid, source, label, ts),
        )


def _correction(db, gid, original, corrected, ts=2000):
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO user_corrections (email_gmail_id, original_label, "
            "corrected_label, created_at) VALUES (?, ?, ?, ?)",
            (gid, original, corrected, ts),
        )


# --- LLM cost -------------------------------------------------------------

def test_record_and_aggregate_llm_cost(db):
    record_llm_usage(db, [
        {"ts": 10, "model": "gpt-4o-mini", "kind": "classify",
         "prompt_tokens": 400, "completion_tokens": 20, "cost_usd": 0.00007, "latency_ms": 1100},
        {"ts": 20, "model": "gpt-4o-mini", "kind": "summarize",
         "prompt_tokens": 70, "completion_tokens": 16, "cost_usd": 0.00002, "latency_ms": 900},
    ])
    agg = analytics_llm_cost(db, since_ts=0)
    assert agg["calls"] == 2
    assert agg["tokens"] == 400 + 20 + 70 + 16
    assert round(agg["cost_usd"], 5) == round(0.00009, 5)
    assert agg["avg_latency_ms"] == 1000
    assert {r["kind"] for r in agg["by_kind"]} == {"classify", "summarize"}


def test_llm_cost_respects_since(db):
    record_llm_usage(db, [{"ts": 5, "model": "m", "kind": "classify",
                           "prompt_tokens": 1, "completion_tokens": 1,
                           "cost_usd": 1.0, "latency_ms": 1}])
    assert analytics_llm_cost(db, since_ts=100)["calls"] == 0
    assert analytics_llm_cost(db, since_ts=0)["calls"] == 1


def test_record_llm_usage_empty_is_noop(db):
    record_llm_usage(db, [])
    assert analytics_llm_cost(db, 0)["calls"] == 0


# --- Per-tier correction rate --------------------------------------------

def test_tier_quality_counts_corrections_per_source(db):
    # rules: 2 predictions, 1 corrected -> 0.5
    _email(db, "r1"); _prediction(db, "r1", "rules", "NEWSLETTER")
    _email(db, "r2"); _prediction(db, "r2", "rules", "NEWSLETTER")
    _correction(db, "r1", "NEWSLETTER", "FINANCE")          # changed -> counts
    # llm: 1 prediction, 1 "correction" that DIDN'T change the label -> 0.0
    _email(db, "l1"); _prediction(db, "l1", "llm", "PERSONAL")
    _correction(db, "l1", "PERSONAL", "PERSONAL")           # same label -> not a correction

    stats = {r["source"]: r for r in analytics_tier_quality(db, 0)}
    assert stats["rules"]["total"] == 2
    assert stats["rules"]["corrections"] == 1
    assert stats["rules"]["correction_rate"] == 0.5
    assert stats["llm"]["corrections"] == 0       # same-label correction doesn't count
    assert stats["llm"]["correction_rate"] == 0.0


def test_tier_quality_dedupes_multiple_corrections(db):
    # One prediction with TWO corrections must still count as a single corrected item.
    _email(db, "e1"); _prediction(db, "e1", "ml", "NOTIFICATION")
    _correction(db, "e1", "NOTIFICATION", "MEETING", ts=2000)
    _correction(db, "e1", "MEETING", "CALENDAR", ts=3000)
    stats = {r["source"]: r for r in analytics_tier_quality(db, 0)}
    assert stats["ml"]["total"] == 1
    assert stats["ml"]["corrections"] == 1        # not 2


# --- Autopilot precision --------------------------------------------------

def test_autopilot_precision(db):
    # Two auto-executed actions; one later corrected to a DIFFERENT label -> 0.5
    _email(db, "a1"); _prediction(db, "a1", "ml", "X")
    _email(db, "a2"); _prediction(db, "a2", "ml", "Z")
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO action_queue (email_gmail_id, action, status, reason_json, created_at) "
            "VALUES (?, ?, 'executed', ?, ?)",
            ("a1", "archive", json.dumps({"reason": "auto-executed"}), 1000),
        )
        cur.execute(
            "INSERT INTO action_queue (email_gmail_id, action, status, reason_json, created_at) "
            "VALUES (?, ?, 'executed', ?, ?)",
            ("a2", "archive", json.dumps({"reason": "auto-executed"}), 1000),
        )
    _correction(db, "a1", "X", "Y")   # Y != predicted X -> a real disagreement
    res = analytics_autopilot_precision(db, 0)
    assert res["auto_executed"] == 2
    assert res["later_corrected"] == 1
    assert res["precision"] == 0.5


def test_autopilot_precision_ignores_confirming_correction(db):
    # A correction whose label MATCHES the prediction is not a miss -> precision 1.0
    _email(db, "a1"); _prediction(db, "a1", "ml", "WORK")
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO action_queue (email_gmail_id, action, status, reason_json, created_at) "
            "VALUES (?, ?, 'executed', ?, ?)",
            ("a1", "archive", json.dumps({"reason": "auto-executed"}), 1000),
        )
    _correction(db, "a1", "WORK", "WORK")   # confirms the prediction
    res = analytics_autopilot_precision(db, 0)
    assert res["auto_executed"] == 1
    assert res["later_corrected"] == 0
    assert res["precision"] == 1.0


def test_autopilot_precision_none_when_no_autoexec(db):
    assert analytics_autopilot_precision(db, 0)["precision"] is None
