"""Tests for the learned sender label prior (get_sender_label_prior in queries.py)."""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from mailmind.storage.database import Database
from mailmind.storage.queries import get_sender_label_prior


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        yield Database(pathlib.Path(d) / "test.db")


def _add_corrections(db: Database, sender: str, label_counts: dict):
    """Insert emails + corrections for a sender. label_counts = {label: n}."""
    i = 0
    for label, n in label_counts.items():
        for _ in range(n):
            gid = f"msg_{sender}_{label}_{i}"
            db.execute_sql(
                "INSERT INTO emails (gmail_id, sender, subject) VALUES (?, ?, ?)",
                (gid, sender, "subject"),
            )
            db.execute_sql(
                "INSERT INTO user_corrections (email_gmail_id, corrected_label) VALUES (?, ?)",
                (gid, label),
            )
            i += 1


def test_prior_abstains_below_min_count(db):
    _add_corrections(db, "rare@x.com", {"WORK": 2})
    assert get_sender_label_prior(db, "rare@x.com", min_count=3) == {}


def test_prior_returns_distribution_above_min_count(db):
    _add_corrections(db, "regular@x.com", {"WORK": 4, "PERSONAL": 1})
    dist = get_sender_label_prior(db, "regular@x.com", min_count=3)
    assert "WORK" in dist
    assert dist["WORK"] > dist["PERSONAL"]
    assert abs(sum(dist.values()) - 1.0) < 1e-9


def test_prior_unknown_sender_abstains(db):
    assert get_sender_label_prior(db, "nobody@unknown.com", min_count=1) == {}


def test_prior_only_counts_corrected_labels(db):
    """Predictions without corrections should NOT feed the prior."""
    sender = "nocorrect@x.com"
    gid = "msg_nc_1"
    db.execute_sql(
        "INSERT INTO emails (gmail_id, sender, subject) VALUES (?, ?, ?)",
        (gid, sender, "Hi"),
    )
    # Insert a prediction but NO correction
    db.execute_sql(
        "INSERT INTO predictions (email_gmail_id, model, primary_label, confidence) "
        "VALUES (?, ?, ?, ?)",
        (gid, "test", "WORK", 0.9),
    )
    assert get_sender_label_prior(db, sender, min_count=1) == {}


def test_prior_normalises(db):
    _add_corrections(db, "norm@x.com", {"FINANCE": 3, "NEWSLETTER": 2, "WORK": 1})
    dist = get_sender_label_prior(db, "norm@x.com", min_count=3)
    assert abs(sum(dist.values()) - 1.0) < 1e-9
    assert dist["FINANCE"] > dist["NEWSLETTER"] > dist["WORK"]


def test_prior_exact_min_count_qualifies(db):
    _add_corrections(db, "exact@x.com", {"CALENDAR": 3})
    dist = get_sender_label_prior(db, "exact@x.com", min_count=3)
    assert "CALENDAR" in dist


def test_prior_respects_account_scope(db):
    """When account is provided, only corrections for emails on that account are used.

    Emails inserted without an account (account=NULL) do not match account='account_a',
    so the query returns 0 rows → abstain ({}).  The function must return a dict.
    """
    _add_corrections(db, "shared@x.com", {"WORK": 5})
    # Emails were inserted without an account column, so filtering on account='account_a'
    # finds nothing → {} (abstain).  This verifies cross-account isolation.
    dist = get_sender_label_prior(db, "shared@x.com", account="account_a", min_count=3)
    assert isinstance(dist, dict)
    # No account match → abstain (emails have NULL account, not 'account_a')
    assert dist == {}
