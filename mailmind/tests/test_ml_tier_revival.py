"""Tests for P1A: the ML tier is wired into the pipeline.

When the ClassifierRouter handles an email via the rules or ML tier, the
paid DeepSeek call must NOT fire. When the model isn't loaded, the legacy
rules->LLM path still works (back-compat).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from mailmind.ml.classifier_router import ClassifierRouter, RoutingResult
from mailmind.processing.pipeline import Pipeline
from mailmind.processing.rules import RulesEngine
from mailmind.processing.scorer import PriorityScorer
from mailmind.storage.database import Database
from mailmind.storage.models import Email


def _email(gid: str = "g1") -> Email:
    return Email(
        gmail_id=gid,
        sender="alice@example.com",
        subject="quick note",
        snippet="hello",
        body_text="body text body text body text",
        recipients=["me@example.com"],
        date_ts=int(datetime.now(timezone.utc).timestamp()),
        labels=[],
        parsed=True,
    )


def _stub_llm_client():
    """A DeepSeek client stub that records every classify_email call."""
    client = MagicMock()
    client.classify_email.return_value = MagicMock(
        model_available=True,
        primary_label="WORK",
        llm_confidence=0.9,
        to_scoring_breakdown_entry=lambda: {"label": "WORK"},
    )
    return client


def _stub_router(source: str, label: str = "NEWSLETTER", conf: float = 0.85):
    """A ClassifierRouter stub that returns a fixed RoutingResult."""
    router = MagicMock(spec=ClassifierRouter)
    router.route.return_value = RoutingResult(
        source=source, label=label, confidence=conf,
    )
    return router


class TestMLTierSkipsLLM:
    def test_ml_tier_handles_email_skips_llm(self):
        """When router returns source='ml', DeepSeek must not be called."""
        db = Database(":memory:")
        try:
            llm = _stub_llm_client()
            router = _stub_router(source="ml", label="NEWSLETTER", conf=0.80)
            pipeline = Pipeline(
                db=db,
                rules_engine=RulesEngine(),
                scorer=PriorityScorer(),
                llm_client=llm,
                llm_skip_threshold=70,
                classifier_router=router,
            )
            email = _email("ml_handled")
            db.insert_email(email)
            pipeline.process(email)

            router.route.assert_called_once()
            llm.classify_email.assert_not_called()  # money saved
        finally:
            db.close()

    def test_rules_tier_handles_email_skips_llm(self):
        """When router returns source='rules' (confident), DeepSeek must not fire."""
        db = Database(":memory:")
        try:
            llm = _stub_llm_client()
            router = _stub_router(source="rules", label="NEWSLETTER", conf=0.95)
            pipeline = Pipeline(
                db=db,
                rules_engine=RulesEngine(),
                scorer=PriorityScorer(),
                llm_client=llm,
                llm_skip_threshold=70,
                classifier_router=router,
            )
            email = _email("rules_handled")
            db.insert_email(email)
            pipeline.process(email)

            llm.classify_email.assert_not_called()
        finally:
            db.close()

    def test_router_fallback_still_calls_llm(self):
        """If router source='fallback' (neither rules nor ML confident), LLM may fire."""
        db = Database(":memory:")
        try:
            llm = _stub_llm_client()
            router = _stub_router(source="fallback", label="OTHER", conf=0.3)
            pipeline = Pipeline(
                db=db,
                rules_engine=RulesEngine(),
                scorer=PriorityScorer(),
                llm_client=llm,
                llm_skip_threshold=70,
                classifier_router=router,
            )
            email = _email("fallback_path")
            db.insert_email(email)
            pipeline.process(email)

            llm.classify_email.assert_called_once()
        finally:
            db.close()

    def test_no_router_legacy_behavior(self):
        """Without a router, the pipeline's existing rules->LLM path is intact."""
        db = Database(":memory:")
        try:
            llm = _stub_llm_client()
            pipeline = Pipeline(
                db=db,
                rules_engine=RulesEngine(),
                scorer=PriorityScorer(),
                llm_client=llm,
                llm_skip_threshold=70,
                classifier_router=None,
            )
            email = _email("legacy")
            db.insert_email(email)
            pred = pipeline.process(email)
            # Pipeline still produces a prediction; LLM gate decided based on
            # rules score alone (unchanged legacy behavior).
            assert pred.primary_label is not None
        finally:
            db.close()


class TestBuildClassifierRouter:
    def test_returns_none_friendly_when_no_model(self, tmp_path, monkeypatch):
        """No model.joblib on disk -> router still built, ml_model is None."""
        from mailmind import main as main_mod

        # Point MLClassifier at an empty dir so .load() returns False.
        monkeypatch.setattr(
            "mailmind.ml.model.DEFAULT_MODEL_DIR", tmp_path
        )
        router = main_mod._build_classifier_router(RulesEngine())
        assert router is not None
        assert router.ml_model is None
        # LLM tier on the router must be off (DeepSeek path is wired separately).
        assert router.llm_enabled is False
