"""Optimization: ClassifierRouter.route reuses pre-computed rule_matches.

Pipeline.process already runs RulesEngine.evaluate(email); route() should not
run it a second time when the caller passes the result in. Behavior must be
identical whether matches are passed or computed internally.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from mailmind.ml.classifier_router import ClassifierRouter
from mailmind.processing.rules import RulesEngine
from mailmind.storage.models import Email


def _email():
    return Email(gmail_id="r1", sender="news@list.com", subject="Weekly digest",
                 body_text="Click here to unsubscribe.")


def test_route_does_not_reevaluate_when_matches_passed():
    rules = RulesEngine()
    real_eval = rules.evaluate
    rules.evaluate = MagicMock(side_effect=real_eval)  # spy
    router = ClassifierRouter(rules_engine=rules)

    email = _email()
    precomputed = real_eval(email)            # pipeline computes once
    rules.evaluate.reset_mock()

    router.route(email, rule_matches=precomputed)
    rules.evaluate.assert_not_called()        # route must NOT evaluate again


def test_route_still_evaluates_when_no_matches_passed():
    rules = RulesEngine()
    rules.evaluate = MagicMock(side_effect=rules.evaluate)
    router = ClassifierRouter(rules_engine=rules)
    router.route(_email())                     # back-compat path
    rules.evaluate.assert_called_once()


def test_route_result_identical_passed_vs_computed():
    rules = RulesEngine()
    router = ClassifierRouter(rules_engine=rules)
    email = _email()
    matches = rules.evaluate(email)

    r_passed = router.route(email, rule_matches=matches)
    r_computed = router.route(email)
    assert r_passed.source == r_computed.source
    assert r_passed.label == r_computed.label
    assert abs((r_passed.confidence or 0) - (r_computed.confidence or 0)) < 1e-9
