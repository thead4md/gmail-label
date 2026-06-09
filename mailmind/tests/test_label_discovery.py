"""Tests for the periodic label-discovery loop (label_discovery + suggestion CRUD)."""
from __future__ import annotations

import pathlib
import tempfile
import time

import pytest

from mailmind.storage.database import Database
from mailmind.storage.queries import (
    save_label_suggestion,
    get_label_suggestions,
    set_label_suggestion_status,
    get_in_use_labels,
)
from mailmind.intelligence.label_discovery import suggest_labels


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        yield Database(pathlib.Path(d) / "test.db")


# ---------------------------------------------------------------------------
# suggestion CRUD
# ---------------------------------------------------------------------------

def test_save_and_get_suggestion(db):
    assert save_label_suggestion(db, "Invoices", rationale="payment mails",
                                 cluster_terms="invoice, payment", email_count=9,
                                 score=0.3) is True
    pend = get_label_suggestions(db, status="pending")
    assert len(pend) == 1
    assert pend[0]["suggested_label"] == "Invoices"
    assert pend[0]["email_count"] == 9


def test_suggestion_idempotent_on_label(db):
    assert save_label_suggestion(db, "Invoices") is True
    # Same label again → ignored (UNIQUE), returns False.
    assert save_label_suggestion(db, "Invoices") is False
    assert len(get_label_suggestions(db, status="pending")) == 1


def test_set_suggestion_status(db):
    save_label_suggestion(db, "Invoices")
    sid = get_label_suggestions(db, status="pending")[0]["id"]
    set_label_suggestion_status(db, sid, "dismissed")
    assert get_label_suggestions(db, status="pending") == []
    assert len(get_label_suggestions(db, status="dismissed")) == 1


def test_in_use_labels_from_predictions(db):
    db.execute_sql("INSERT INTO emails (gmail_id, subject) VALUES ('m1','s')")
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO predictions (email_gmail_id, model, primary_label, confidence) "
            "VALUES ('m1','t','FINANCE',0.9)"
        )
    in_use = get_in_use_labels(db)
    assert "finance" in in_use


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def _seed_two_themes(db, n=8):
    """Insert two clearly-separable themes of generic-labelled mail."""
    now = int(time.time())
    invoice = ("Invoice payment due", "Your invoice payment is due, billing amount total balance")
    soccer = ("Soccer practice match", "Soccer team practice match training pitch coach players")
    i = 0
    for subj, body in ([invoice] * n + [soccer] * n):
        i += 1
        db.execute_sql(
            "INSERT INTO emails (gmail_id, subject, snippet, body_text, date_ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"msg{i}", f"{subj} {i}", body, body, now),
        )
    # commit
    db.set_state("_seed", "done")


def test_suggest_labels_finds_clusters_keyword_named(db):
    _seed_two_themes(db, n=8)
    added = suggest_labels(db, window_days=60, max_suggestions=5,
                           min_cluster_size=5, llm_client=None)
    assert added, "expected at least one suggestion"
    pend = get_label_suggestions(db, status="pending")
    assert len(pend) >= 1
    # Keyword-named labels should reflect the seeded vocab.
    joined = " ".join(s["suggested_label"].lower() + " " + (s["cluster_terms"] or "")
                      for s in pend)
    assert "invoice" in joined or "soccer" in joined or "payment" in joined


def test_suggest_labels_skips_already_in_use(db):
    _seed_two_themes(db, n=8)
    # Pre-mark both likely theme names as in-use via predictions so discovery dedups.
    db.execute_sql("INSERT INTO emails (gmail_id, subject) VALUES ('x','s')")
    with db.transaction() as cur:
        for lbl in ("Invoice_Payment", "Soccer_Practice", "Payment_Invoice", "Soccer_Team"):
            cur.execute(
                "INSERT INTO predictions (email_gmail_id, model, primary_label, confidence) "
                "VALUES (?, 't', ?, 0.9)", (f"x_{lbl}", lbl),
            )
    # Not asserting zero (naming varies), but the call must not raise and must
    # never re-propose an in-use label.
    added = suggest_labels(db, window_days=60, llm_client=None)
    in_use = get_in_use_labels(db)
    for s in get_label_suggestions(db, status="pending"):
        assert s["suggested_label"].lower() not in in_use


def test_suggest_labels_too_little_data_returns_empty(db):
    db.execute_sql(
        "INSERT INTO emails (gmail_id, subject, date_ts) VALUES ('only','hi',?)",
        (int(time.time()),),
    )
    assert suggest_labels(db, window_days=60, min_cluster_size=6, llm_client=None) == []
