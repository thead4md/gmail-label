"""Tests for folding content rules into the content channel (fold_rules_into_content).

Covers:
- build_content_distribution math: ml-only, rule-only, mixed, abstain, normalisation
- Router with fold OFF reproduces the legacy Tier-1 rules short-circuit (source='rules')
- Router with fold ON: a confident rule no longer pre-empts the blend (source='blend')
- Router with fold ON and no ML still labels via the rule-only content distribution
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from mailmind.ml.classifier_router import ClassifierRouter
from mailmind.ml.sender_channel import build_content_distribution
from mailmind.processing.rules import RuleMatch
from mailmind.storage.models import Email


# ---------------------------------------------------------------------------
# build_content_distribution math
# ---------------------------------------------------------------------------

def test_content_dist_ml_only():
    ml = {"OE": 0.7, "NEWSLETTER": 0.3}
    assert build_content_distribution(ml, None, 0.0) == ml


def test_content_dist_rule_only_when_ml_abstains():
    """No ML proba → the rule vote becomes the content distribution."""
    dist = build_content_distribution({}, "NEWSLETTER", 0.9)
    assert dist == {"NEWSLETTER": 1.0}


def test_content_dist_abstains_when_nothing():
    assert build_content_distribution({}, None, 0.0) == {}
    assert build_content_distribution({}, "X", 0.0) == {}  # zero-confidence rule


def test_content_dist_mix_ml_leads():
    """Default rule_weight=0.30 keeps ML in the lead but lets the rule pull mass."""
    ml = {"OE": 0.6, "NEWSLETTER": 0.4}
    dist = build_content_distribution(ml, "NEWSLETTER", 1.0, rule_weight=0.30)
    # NEWSLETTER mass = 0.7*0.4 + 0.3*1.0 = 0.58 ; OE = 0.7*0.6 = 0.42
    assert abs(sum(dist.values()) - 1.0) < 1e-9
    assert dist["NEWSLETTER"] > dist["OE"]
    assert abs(dist["NEWSLETTER"] - 0.58) < 1e-9


def test_content_dist_strong_ml_survives_weak_rule():
    ml = {"OE": 0.95, "NEWSLETTER": 0.05}
    dist = build_content_distribution(ml, "NEWSLETTER", 0.5, rule_weight=0.30)
    # OE = 0.7*0.95 = 0.665 ; NEWSLETTER = 0.7*0.05 + 0.3*0.5 = 0.185
    assert dist["OE"] > dist["NEWSLETTER"]


def test_content_dist_rule_weight_one_is_rule_only():
    ml = {"OE": 0.9, "NEWSLETTER": 0.1}
    dist = build_content_distribution(ml, "NEWSLETTER", 1.0, rule_weight=1.0)
    assert dist["NEWSLETTER"] > dist["OE"]


# ---------------------------------------------------------------------------
# Router integration
# ---------------------------------------------------------------------------

def _ml_result(label, confidence, proba):
    from mailmind.ml.inference import MLResult
    return MLResult(
        primary_label=label,
        ml_confidence=confidence,
        label_probabilities=proba,
        pipeline_used="ml",
        model_available=True,
    )


@contextmanager
def _router(fold, ml=None):
    """Router whose rules engine emits a confident NEWSLETTER rule (0.90)."""
    rules_engine = MagicMock()
    rules_engine.evaluate.return_value = [
        RuleMatch(rule_name="newsletter", matched=True,
                  labels=["NEWSLETTER"], confidence=0.90),
    ]
    ml_model = MagicMock()
    ml_model.is_fitted = ml is not None

    router = ClassifierRouter(
        rules_engine=rules_engine,
        ml_model=ml_model if ml is not None else None,
        blend_enabled=True,
        fold_rules_into_content=fold,
        content_rule_weight=0.30,
        sender_prior_min_count=3,
    )
    if ml is not None:
        with patch("mailmind.ml.inference.predict_label", return_value=ml):
            yield router
    else:
        yield router


def test_fold_off_rule_short_circuits():
    """Legacy: a 0.90 content rule short-circuits at Tier 1 → source='rules'."""
    ml = _ml_result("OE", 0.95, {"OE": 0.95, "NEWSLETTER": 0.05})
    with _router(fold=False, ml=ml) as router:
        email = Email(gmail_id="a", sender="x@corp.com", subject="Bulletin")
        result = router.route(email, rule_matches=None, db=None)
    assert result.source == "rules"
    assert result.label == "NEWSLETTER"


def test_fold_on_rule_does_not_short_circuit():
    """Fold on: the same rule no longer pre-empts; ML-led blend wins → source='blend'."""
    ml = _ml_result("OE", 0.95, {"OE": 0.95, "NEWSLETTER": 0.05})
    with _router(fold=True, ml=ml) as router:
        email = Email(gmail_id="b", sender="x@corp.com", subject="Bulletin")
        result = router.route(email, rule_matches=None, db=None)
    assert result.source == "blend"
    # OE content mass (0.7*0.95=0.665) beats NEWSLETTER (0.7*0.05+0.3*0.9=0.305)
    assert result.label == "OE"
    assert result.content_distribution is not None


def test_fold_on_rule_pulls_when_ml_unsure():
    """Fold on: when ML is split, the rule vote tips the blend to NEWSLETTER."""
    ml = _ml_result("OE", 0.52, {"OE": 0.52, "NEWSLETTER": 0.48})
    with _router(fold=True, ml=ml) as router:
        email = Email(gmail_id="c", sender="x@corp.com", subject="Bulletin")
        result = router.route(email, rule_matches=None, db=None)
    assert result.source == "blend"
    assert result.label == "NEWSLETTER"


def test_fold_on_rule_only_no_ml_still_labels():
    """Fold on with no ML model: rule-only content still yields a blend label."""
    with _router(fold=True, ml=None) as router:
        email = Email(gmail_id="d", sender="x@corp.com", subject="Bulletin")
        result = router.route(email, rule_matches=None, db=None)
    assert result.source == "blend"
    assert result.label == "NEWSLETTER"
    assert abs(result.confidence - 1.0) < 1e-9
