"""Regression tests for the second-pass bug hunt fixes.

Covers:
- C1: approving after a label correction applies the CORRECTED label to Gmail,
      not the model's stale prediction.
- Q1: set_sender_label_rule replaces (not duplicates) when account IS NULL.
- Q2: get_queue_stats counts 'execute_failed' under the correct key.
- queue_manager: auto-executed rows are stamped with the email's account.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mailmind.intelligence.feedback import handle_approve, handle_correction
from mailmind.processing.queue_manager import QueueManager
from mailmind.processing.scorer import ScoreResult
from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction
from mailmind.storage.queries import (
    get_queue_stats,
    set_sender_label_rule,
    update_sender_profile,
    toggle_sender_auto_action,
)


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def _seed_queue_item(db: Database, *, primary_label: str = "WORK",
                     confidence: float = 0.95) -> int:
    db.insert_email(Email(
        gmail_id="g1", sender="alice@example.com", subject="subj", snippet="x",
        body_text="b", recipients=["me@example.com"], date_ts=1, labels=[],
        parsed=True, account="me@example.com",
    ))
    db.save_prediction(Prediction(
        email_gmail_id="g1", model="rules", labels=[primary_label],
        priority_score=int(confidence * 100), primary_label=primary_label,
        confidence=confidence, pipeline_used="rules", rule_matches=[],
        scoring_breakdown="{}", account="me@example.com",
    ))
    cur = db.execute_sql(
        "INSERT INTO action_queue "
        "(email_gmail_id, action, status, confidence, priority_score) "
        "VALUES (?, ?, 'pending', ?, ?)",
        ("g1", "label", confidence, int(confidence * 100)),
    )
    db._conn.commit()
    return cur.lastrowid


# ── C1 ──────────────────────────────────────────────────────────────────────
class TestCorrectedLabelIsApplied:
    def test_corrected_label_wins_over_prediction(self, db: Database):
        qid = _seed_queue_item(db, primary_label="WORK")
        # User corrects WORK -> FINANCE in the UI, then approves.
        handle_correction(db, qid, corrected_label="FINANCE")
        executor = MagicMock()
        executor.execute_action.return_value = True

        handle_approve(db, qid, executor=executor)

        _email, action, score = executor.execute_action.call_args.args
        assert action == "label"
        # The corrected label, not the original "WORK", reaches Gmail.
        assert score.primary_label == "FINANCE"

    def test_no_correction_uses_prediction(self, db: Database):
        qid = _seed_queue_item(db, primary_label="WORK")
        executor = MagicMock()
        executor.execute_action.return_value = True

        handle_approve(db, qid, executor=executor)

        _email, _action, score = executor.execute_action.call_args.args
        assert score.primary_label == "WORK"


# ── Q1 ──────────────────────────────────────────────────────────────────────
class TestSenderRuleReplaceNullAccount:
    def _count(self, db: Database) -> int:
        return db.execute_sql(
            "SELECT COUNT(*) c FROM sender_label_rules WHERE sender_email = ?",
            ("acct@x.com",),
        ).fetchone()["c"]

    def test_null_account_replaces_not_duplicates(self, db: Database):
        set_sender_label_rule(db, "acct@x.com", "FINANCE")  # account=None
        set_sender_label_rule(db, "acct@x.com", "FINANCE")  # same -> replace
        assert self._count(db) == 1

    def test_null_account_updates_pattern_in_place(self, db: Database):
        set_sender_label_rule(db, "acct@x.com", "FINANCE", match_pattern="invoice")
        set_sender_label_rule(db, "acct@x.com", "FINANCE", match_pattern="receipt")
        assert self._count(db) == 1
        row = db.execute_sql(
            "SELECT match_pattern FROM sender_label_rules WHERE sender_email = ?",
            ("acct@x.com",),
        ).fetchone()
        assert row["match_pattern"] == "receipt"

    def test_distinct_labels_keep_separate_rows(self, db: Database):
        set_sender_label_rule(db, "acct@x.com", "FINANCE")
        set_sender_label_rule(db, "acct@x.com", "WORK")
        assert self._count(db) == 2


# ── Q2 ──────────────────────────────────────────────────────────────────────
class TestQueueStatsExecuteFailed:
    def test_execute_failed_is_counted(self, db: Database):
        db.execute_sql(
            "INSERT INTO action_queue (email_gmail_id, action, status) "
            "VALUES ('g1', 'label', 'execute_failed')",
        )
        db._conn.commit()
        stats = get_queue_stats(db)
        assert stats["execute_failed"] == 1


# ── queue_manager account stamp ──────────────────────────────────────────────
class TestAutoExecuteStampsAccount:
    def test_auto_executed_row_has_account(self, db: Database):
        db.insert_email(Email(
            gmail_id="g2", sender="bob@example.com", subject="s", snippet="x",
            body_text="b", recipients=["me@example.com"], date_ts=1, labels=[],
            parsed=True, account="me@example.com",
        ))
        pred = Prediction(
            email_gmail_id="g2", model="rules", labels=["NEWSLETTER"],
            priority_score=95, primary_label="NEWSLETTER", confidence=0.95,
            pipeline_used="rules", rule_matches=[], scoring_breakdown="{}",
            action_suggested="archive", account="me@example.com",
        )
        pred.id = db.save_prediction(pred)
        # Sender must be opted into autopilot for auto-execute to fire.
        update_sender_profile(db, "bob@example.com", "seen")
        toggle_sender_auto_action(db, "bob@example.com", True)

        email = db.get_email_by_gmail_id("g2")
        email_obj = Email(
            gmail_id="g2", sender="bob@example.com", subject="s", snippet="x",
            body_text="b", recipients=["me@example.com"], date_ts=1, labels=[],
            parsed=True, account="me@example.com",
        )
        score = ScoreResult(
            total_score=95, base_score=95, rule_contribution=0,
            direct_mention_bonus=0, recency_bonus=0, sender_trust=0,
            primary_label="NEWSLETTER",
        )
        qm = QueueManager(executor=MagicMock(execute_action=MagicMock(return_value=True)))
        status = qm.enqueue_from_prediction(db, email_obj, score, pred)

        assert status == "executed"
        row = db.execute_sql(
            "SELECT account FROM action_queue WHERE email_gmail_id = 'g2' "
            "AND status = 'executed'",
        ).fetchone()
        assert row["account"] == "me@example.com"
