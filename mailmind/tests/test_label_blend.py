"""Tests for the 80/20 content/sender blend in ClassifierRouter.

Key assertions:
- blend_distributions math: content wins when it should, sender nudges ties
- sender abstains → pure content label
- finance-domain signal populates sender channel
- blend_enabled=False reproduces old cascade (no blend source)
- Tier 0 explicit rules still hard-override before any blend
"""
from __future__ import annotations

import pathlib
import tempfile
from unittest.mock import MagicMock

import pytest

from mailmind.ml.classifier_router import ClassifierRouter
from mailmind.ml.sender_channel import blend_distributions, build_sender_distribution
from mailmind.processing.rules import RuleMatch
from mailmind.storage.database import Database
from mailmind.storage.models import Email


# ---------------------------------------------------------------------------
# blend_distributions math
# ---------------------------------------------------------------------------

def test_blend_content_wins_clearly():
    """Content PERSONAL@0.70 vs sender FINANCE@1.0 → PERSONAL wins (0.8*0.70 > 0.2*1.0)."""
    p_content = {"PERSONAL": 0.70, "FINANCE": 0.20, "OTHER": 0.10}
    p_sender  = {"FINANCE": 1.0}
    result = blend_distributions(p_content, p_sender)
    assert result["PERSONAL"] > result["FINANCE"], (
        f"Content should dominate: {result}"
    )
    assert abs(sum(result.values()) - 1.0) < 1e-9, "Must be normalised"


def test_blend_sender_abstain_returns_pure_content():
    """When p_sender is empty, blend == p_content exactly."""
    p_content = {"NEWSLETTER": 0.80, "OTHER": 0.20}
    result = blend_distributions(p_content, {})
    assert result == p_content


def test_blend_sender_nudges_tie():
    """Content is split 50/50, sender has strong FINANCE signal → FINANCE wins."""
    p_content = {"FINANCE": 0.50, "WORK": 0.50}
    p_sender  = {"FINANCE": 0.90, "OTHER": 0.10}
    result = blend_distributions(p_content, p_sender)
    assert result["FINANCE"] > result["WORK"]


def test_blend_normalises_to_one():
    p_content = {"A": 0.6, "B": 0.4}
    p_sender  = {"A": 0.3, "C": 0.7}
    result = blend_distributions(p_content, p_sender)
    assert abs(sum(result.values()) - 1.0) < 1e-9
    assert "C" in result  # labels from sender channel appear in output


def test_blend_custom_weights():
    """With weights flipped (20% content / 80% sender), sender wins."""
    p_content = {"PERSONAL": 0.90, "FINANCE": 0.10}
    p_sender  = {"FINANCE": 1.0}
    result = blend_distributions(p_content, p_sender, content_weight=0.20, sender_weight=0.80)
    assert result["FINANCE"] > result["PERSONAL"]


# ---------------------------------------------------------------------------
# build_sender_distribution
# ---------------------------------------------------------------------------

def test_build_sender_finance_domain():
    """Known finance domain → FINANCE prior without DB lookup."""
    dist = build_sender_distribution("billing@paypal.com", db=None)
    assert "FINANCE" in dist
    assert dist["FINANCE"] > 0.5


def test_build_sender_unknown_domain_no_db():
    """Unknown sender, no DB → abstain."""
    dist = build_sender_distribution("alice@unknown-corp.com", db=None)
    assert dist == {}


def test_build_sender_learned_prior():
    """Sender with enough user corrections returns a populated distribution."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = Database(pathlib.Path(d) / "test.db")
        # Insert an email and three corrections for the same sender
        db.execute_sql(
            "INSERT INTO emails (gmail_id, sender, subject) VALUES (?, ?, ?)",
            ("msg1", "boss@corp.com", "Hello"),
        )
        db.execute_sql(
            "INSERT INTO emails (gmail_id, sender, subject) VALUES (?, ?, ?)",
            ("msg2", "boss@corp.com", "Hello 2"),
        )
        db.execute_sql(
            "INSERT INTO emails (gmail_id, sender, subject) VALUES (?, ?, ?)",
            ("msg3", "boss@corp.com", "Hello 3"),
        )
        for gid, lbl in [("msg1", "WORK"), ("msg2", "WORK"), ("msg3", "WORK")]:
            db.execute_sql(
                "INSERT INTO user_corrections (email_gmail_id, corrected_label) VALUES (?, ?)",
                (gid, lbl),
            )
        dist = build_sender_distribution("boss@corp.com", db=db, min_count=3)
        assert "WORK" in dist
        assert dist["WORK"] > 0.7


def test_build_sender_below_min_count_abstains():
    """Fewer than min_count corrections → abstain."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = Database(pathlib.Path(d) / "test.db")
        db.execute_sql(
            "INSERT INTO emails (gmail_id, sender, subject) VALUES (?, ?, ?)",
            ("msg1", "sparse@example.com", "Hi"),
        )
        db.execute_sql(
            "INSERT INTO user_corrections (email_gmail_id, corrected_label) VALUES (?, ?)",
            ("msg1", "PERSONAL"),
        )
        dist = build_sender_distribution("sparse@example.com", db=db, min_count=3)
        assert dist == {}


# ---------------------------------------------------------------------------
# Router blend integration
# ---------------------------------------------------------------------------

from contextlib import contextmanager
from unittest.mock import patch


def _make_fake_ml_result(label, confidence, label_proba=None):
    from mailmind.ml.inference import MLResult
    if label_proba is None:
        label_proba = {label: confidence, "OTHER": round(1 - confidence, 4)}
    return MLResult(
        primary_label=label,
        ml_confidence=confidence,
        label_probabilities=label_proba,
        pipeline_used="ml",
        model_available=True,
    )


@contextmanager
def _router_ctx(ml_label="PERSONAL", ml_conf=0.70, ml_proba=None,
                blend_enabled=True, content_weight=0.80, sender_weight=0.20):
    if ml_proba is None:
        ml_proba = {ml_label: ml_conf, "OTHER": round(1 - ml_conf, 4)}
    fake_result = _make_fake_ml_result(ml_label, ml_conf, ml_proba)

    rules_engine = MagicMock()
    rules_engine.evaluate.return_value = []

    ml_model = MagicMock()
    ml_model.is_fitted = True

    router = ClassifierRouter(
        rules_engine=rules_engine,
        ml_model=ml_model,
        blend_enabled=blend_enabled,
        content_weight=content_weight,
        sender_weight=sender_weight,
        sender_prior_min_count=3,
    )
    with patch("mailmind.ml.inference.predict_label", return_value=fake_result):
        yield router


def test_router_blend_source_returned():
    """Router with blend_enabled returns source='blend'."""
    with _router_ctx() as router:
        email = Email(gmail_id="x1", sender="random@example.com", subject="Hi")
        result = router.route(email, rule_matches=[], db=None)
    assert result.source == "blend"


def test_router_blend_disabled_returns_ml():
    """Router with blend_enabled=False and confident ML returns source='ml'."""
    with _router_ctx(ml_conf=0.80, blend_enabled=False) as router:
        email = Email(gmail_id="x2", sender="random@example.com", subject="Hi")
        result = router.route(email, rule_matches=[], db=None)
    assert result.source == "ml"


def test_router_tier0_still_hard_overrides():
    """Explicit sender rule (Tier 0) must win even with blend_enabled=True."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = Database(pathlib.Path(d) / "test.db")
        from mailmind.storage.queries import set_sender_label_rule
        set_sender_label_rule(db, "pinned@corp.com", "FINANCE")

        with _router_ctx() as router:
            email = Email(gmail_id="x3", sender="pinned@corp.com", subject="Invoice")
            result = router.route(email, rule_matches=[], db=db)
    assert result.source == "rule"
    assert result.label == "FINANCE"
    assert result.confidence == 1.0


def test_router_blend_finance_domain_nudges():
    """Finance-domain sender nudges toward FINANCE even when content splits the vote.

    ml_conf=0.70 clears the blend entry floor (0.65). FINANCE leads content at 0.50
    and the finance-domain sender boosts it further — blend must return FINANCE.
    """
    ml_proba = {"FINANCE": 0.50, "WORK": 0.35, "OTHER": 0.15}
    with _router_ctx(ml_label="FINANCE", ml_conf=0.70, ml_proba=ml_proba) as router:
        email = Email(gmail_id="x4", sender="payments@paypal.com", subject="Your payment")
        result = router.route(email, rule_matches=[], db=None)
    assert result.source == "blend"
    assert result.label == "FINANCE"
