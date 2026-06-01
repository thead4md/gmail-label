"""Tests for the explainer module and reason_json structure.

Covers:
- ReasonPayload has all required fields
- to_json() is valid JSON with all expected keys
- Thread context fields propagate correctly
- reason_json is stored in queue items and round-trips through upsert
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction, QueueItem
from mailmind.intelligence.explainer import ReasonPayload, build_reason_payload
from mailmind.storage.queries import upsert_queue_item, get_queue_item_by_fingerprint
from mailmind.utils.fingerprint import make_action_fingerprint

_REASON_KEYS = (
    "primary_label",
    "score",
    "score_breakdown",
    "rule_matches",
    "ml_confidence",
    "llm_confidence",
    "trust_tier",
    "thread_summary",
    "reply_needed",
    "similar_past_actions",
)


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Database(Path(tmpdir) / "test.db")
        yield d
        d.close()


@pytest.fixture
def base_email(db):
    e = Email(gmail_id="exp1", sender="alice@test.com", subject="Test", snippet="")
    db.insert_email(e)
    return e


@pytest.fixture
def base_prediction(db, base_email):
    pred = Prediction(
        email_gmail_id=base_email.gmail_id,
        model="rules",
        labels=["WORK"],
        priority_score=75,
        primary_label="WORK",
        rule_matches=["work_keyword"],
        ml_confidence=0.72,
        llm_confidence=0.88,
    )
    pred.id = db.save_prediction(pred)
    return pred


def test_reason_payload_has_required_fields(db, base_email, base_prediction):
    payload = build_reason_payload(db, base_prediction)

    assert payload.primary_label == "WORK"
    assert isinstance(payload.score, int)
    assert isinstance(payload.rule_matches, list)
    assert isinstance(payload.score_breakdown, dict)
    assert isinstance(payload.trust_tier, str)
    assert isinstance(payload.reply_needed, bool)
    assert isinstance(payload.similar_past_actions, list)
    assert payload.ml_confidence == 0.72
    assert payload.llm_confidence == 0.88


def test_to_json_is_valid_and_complete(db, base_email, base_prediction):
    payload = build_reason_payload(db, base_prediction)
    json_str = payload.to_json()

    obj = json.loads(json_str)
    for key in _REASON_KEYS:
        assert key in obj, f"reason_json missing key: {key}"


def test_thread_context_propagates(db, base_email, base_prediction):
    thread_ctx = {"thread_summary": "Waiting for sign-off", "reply_needed": True}
    payload = build_reason_payload(db, base_prediction, thread_ctx)

    assert payload.thread_summary == "Waiting for sign-off"
    assert payload.reply_needed is True


def test_thread_context_absent_gives_safe_defaults(db, base_email, base_prediction):
    payload = build_reason_payload(db, base_prediction, thread_context=None)

    assert payload.thread_summary is None
    assert payload.reply_needed is False


def test_reason_json_stored_and_retrieved_in_queue_item(db, base_email, base_prediction):
    payload = build_reason_payload(db, base_prediction)
    reason_obj = json.loads(payload.to_json())

    fp = make_action_fingerprint(base_email.gmail_id, "star", {})
    qi = QueueItem(
        email_gmail_id=base_email.gmail_id,
        prediction_id=base_prediction.id,
        action="star",
        params={},
        action_fingerprint=fp,
        status="pending",
        confidence=0.75,
        priority_score=75,
        reason_json=reason_obj,
    )
    result = upsert_queue_item(db, qi)

    assert result is not None
    assert result.reason_json is not None
    assert result.reason_json.get("primary_label") == "WORK"
    assert "score" in result.reason_json
    assert "trust_tier" in result.reason_json


def test_reason_payload_trust_tier_reflects_sender_profile(db, base_email, base_prediction):
    """Sender profile trust tier appears in reason payload."""
    from mailmind.storage.queries import update_sender_profile

    # Make alice trusted (5 approvals)
    for _ in range(5):
        update_sender_profile(db, base_email.sender, "approved")

    payload = build_reason_payload(db, base_prediction)
    assert payload.trust_tier == "trusted"
