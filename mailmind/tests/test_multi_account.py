"""Tests for multi-account (second mailbox) support.

Covers the `account` dimension added to emails/predictions/action_queue and
the account-aware read queries. Sender data is intentionally shared across
accounts, so it is not account-scoped.
"""
from __future__ import annotations

import pytest

from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def _email(gmail_id: str, account: str) -> Email:
    return Email(
        gmail_id=gmail_id,
        sender="s@example.com",
        subject="subj",
        snippet="x",
        body_text="body",
        recipients=["me@example.com"],
        date_ts=1,
        labels=[],
        parsed=True,
        account=account,
    )


def _pred(gmail_id: str, account: str, label: str = "WORK") -> Prediction:
    return Prediction(
        email_gmail_id=gmail_id,
        account=account,
        model="rules",
        labels=[label],
        priority_score=50,
        primary_label=label,
        confidence=0.9,
        pipeline_used="rules",
        rule_matches=[],
        scoring_breakdown="{}",
    )


class TestAccountWrites:
    def test_insert_email_persists_account(self, db: Database):
        db.insert_email(_email("g1", "a@x.com"))
        assert db.get_email_by_gmail_id("g1")["account"] == "a@x.com"

    def test_save_prediction_persists_account(self, db: Database):
        db.insert_email(_email("g1", "a@x.com"))
        db.save_prediction(_pred("g1", "a@x.com"))
        assert db.get_predictions_for_email("g1")[0]["account"] == "a@x.com"

    def test_two_accounts_coexist(self, db: Database):
        db.insert_email(_email("g1", "a@x.com"))
        db.insert_email(_email("g2", "b@y.com"))
        db.save_prediction(_pred("g1", "a@x.com"))
        db.save_prediction(_pred("g2", "b@y.com"))

        a_count = db.execute_sql(
            "SELECT COUNT(*) c FROM predictions WHERE account = 'a@x.com'"
        ).fetchone()["c"]
        b_count = db.execute_sql(
            "SELECT COUNT(*) c FROM predictions WHERE account = 'b@y.com'"
        ).fetchone()["c"]
        assert a_count == 1
        assert b_count == 1
