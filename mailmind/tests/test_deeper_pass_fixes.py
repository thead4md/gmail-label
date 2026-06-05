"""Regression tests for the second-pass bug hunt fixes.

Covers:
- C1: approving after a label correction applies the CORRECTED label to Gmail,
      not the model's stale prediction.
- Q1: set_sender_label_rule replaces (not duplicates) when account IS NULL.
- Q2: get_queue_stats counts 'execute_failed' under the correct key.
- queue_manager: auto-executed rows are stamped with the email's account.
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

from mailmind.ingestion.parser import parse_message
from mailmind.intelligence.feedback import (
    handle_approve,
    handle_correction,
    handle_know_sender,
    handle_mute_sender,
)
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


def _trust_tier(db: Database, sender: str):
    row = db.execute_sql(
        "SELECT trust_tier FROM sender_profiles WHERE sender_email = ?", (sender,)
    ).fetchone()
    return row["trust_tier"] if row else None


def _msg(headers, *, internal_date=None, parts=None, body=None, mime="text/plain"):
    """Build a minimal Gmail message resource for parse_message."""
    payload = {"mimeType": "multipart/mixed" if parts else mime,
               "headers": [{"name": k, "value": v} for k, v in headers.items()]}
    if parts is not None:
        payload["parts"] = parts
    elif body is not None:
        payload["body"] = {"data": body}
    res = {"id": "m1", "threadId": "t1", "snippet": "s", "labelIds": [], "payload": payload}
    if internal_date is not None:
        res["internalDate"] = internal_date
    return res


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


# ── C2: manual trust tier is sticky ──────────────────────────────────────────
class TestManualTrustTierSticky:
    def test_known_sender_survives_approve_recompute(self, db: Database):
        handle_know_sender(db, "vip@x.com")
        assert _trust_tier(db, "vip@x.com") == "trusted"
        # An approve would normally recompute the tier (total_seen < 5 -> neutral).
        update_sender_profile(db, "vip@x.com", "approved")
        assert _trust_tier(db, "vip@x.com") == "trusted"   # not reverted

    def test_muted_sender_survives_reject_recompute(self, db: Database):
        handle_mute_sender(db, "spam@x.com")
        assert _trust_tier(db, "spam@x.com") == "watchlist"
        update_sender_profile(db, "spam@x.com", "rejected")
        assert _trust_tier(db, "spam@x.com") == "watchlist"

    def test_auto_tier_still_recomputes(self, db: Database):
        # A sender that was never manually set should still auto-promote.
        for _ in range(6):
            update_sender_profile(db, "auto@x.com", "approved")
        assert _trust_tier(db, "auto@x.com") == "trusted"


# ── Parser fixes ─────────────────────────────────────────────────────────────
class TestParserFixes:
    def test_recipients_with_comma_display_name(self):
        # I1: "Last, First" must not shatter into bogus addresses.
        email = parse_message(_msg({
            "From": "a@x.com",
            "To": '"Doe, John" <john@x.com>, jane@y.com',
            "Subject": "hi",
        }))
        assert email.recipients == ["john@x.com", "jane@y.com"]

    def test_internal_date_preferred_and_in_seconds(self):
        # I2: internalDate is epoch ms; date_ts must be the //1000 seconds value.
        email = parse_message(_msg({"From": "a@x.com", "Subject": "hi"},
                                   internal_date="1700000000000"))
        assert email.date_ts == 1700000000

    def test_naive_date_header_coerced_utc(self):
        # I2 fallback: a timezone-less Date header is read as UTC, not server-local.
        email = parse_message(_msg({
            "From": "a@x.com", "Subject": "hi",
            "Date": "Mon, 01 Jan 2024 00:00:00",
        }))
        assert email.date_ts == 1704067200  # 2024-01-01T00:00:00Z

    def test_stub_plain_falls_back_to_html(self):
        # I3: empty text/plain stub must not suppress the real HTML body.
        b64 = lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")
        email = parse_message(_msg({"From": "a@x.com", "Subject": "hi"}, parts=[
            {"mimeType": "text/plain", "body": {"data": b64("   ")}},
            {"mimeType": "text/html", "body": {"data": b64("<p>Real body</p>")}},
        ]))
        assert email.body_text == "Real body"

    def test_rfc2047_subject_decoded(self):
        # I5: encoded-word subject decodes instead of storing mojibake.
        email = parse_message(_msg({
            "From": "a@x.com", "To": "b@x.com",
            "Subject": "=?UTF-8?B?SGVsbG8gV29ybGQ=?=",
        }))
        assert email.subject == "Hello World"
