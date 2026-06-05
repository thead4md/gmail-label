"""Phase 1: content tiers must be reachable.

- Label-free rules (e.g. directly_addressed @0.95) must NOT short-circuit Tier 1;
  such mail falls through to ML/LLM instead of defaulting to NOTIFICATION.
- Label-assigning rules (newsletter/finance/...) still short-circuit unchanged.
- The LLM provider switch (_build_llm_client) picks OpenAI vs DeepSeek correctly.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from mailmind.ml.classifier_router import ClassifierRouter
from mailmind.processing.rules import RuleMatch
from mailmind.storage.models import Email


def _router(rule_matches):
    rules_engine = MagicMock()
    rules_engine.evaluate.return_value = rule_matches
    # ml_model=None, llm disabled → if Tier 1 doesn't fire we land in "fallback",
    # which is the observable signal that content tiers would run.
    return ClassifierRouter(rules_engine=rules_engine, rules_threshold=0.90)


def test_label_free_rule_does_not_short_circuit():
    """directly_addressed (0.95, no labels) must not win Tier 1."""
    matches = [RuleMatch(rule_name="directly_addressed", matched=True,
                         labels=[], score_delta=10, confidence=0.95)]
    router = _router(matches)
    email = Email(gmail_id="a", sender="boss@corp.com", subject="Quick question")
    result = router.route(email, rule_matches=matches, db=None)
    assert result.source != "rules"          # did NOT short-circuit on the label-free rule
    assert result.source == "fallback"        # would proceed to content tiers


def test_labeling_rule_still_short_circuits():
    """A real labeling rule at >=0.90 still wins Tier 1 (unchanged behavior)."""
    matches = [RuleMatch(rule_name="newsletter_unsubscribe", matched=True,
                         labels=["NEWSLETTER"], score_delta=-15, confidence=0.90)]
    router = _router(matches)
    email = Email(gmail_id="b", sender="news@list.com", subject="Weekly digest")
    result = router.route(email, rule_matches=matches, db=None)
    assert result.source == "rules"
    assert result.label == "NEWSLETTER"


def test_label_free_plus_low_conf_labeling_rule_falls_through():
    """directly_addressed(0.95,no label) + calendar(0.85,CALENDAR): 0.85<0.90 → fall through."""
    matches = [
        RuleMatch(rule_name="directly_addressed", matched=True, labels=[], confidence=0.95),
        RuleMatch(rule_name="calendar_invite", matched=True, labels=["CALENDAR"], confidence=0.85),
    ]
    router = _router(matches)
    email = Email(gmail_id="c", sender="cal@corp.com", subject="Invite: sync")
    result = router.route(email, rule_matches=matches, db=None)
    assert result.source != "rules"


class _Cfg:
    """Minimal stand-in for MailMindConfig."""
    def __init__(self, openai_key="", deepseek_enabled=False):
        self.openai_api_key = openai_key
        self.openai_model = "gpt-4o-mini"
        self.deepseek_api_key = "dk" if deepseek_enabled else ""
        self.deepseek_model = "deepseek-chat"
        self.llm_enabled = deepseek_enabled


def test_provider_switch_auto_prefers_openai_when_key_present():
    from mailmind import main
    cfg = _Cfg(openai_key="sk-test", deepseek_enabled=True)
    with patch.dict("os.environ", {"LLM_PROVIDER": "auto"}, clear=False), \
         patch("mailmind.ml.llm_classifier.LLMClassifier") as MockClf:
        client = main._build_llm_client(cfg)
    from mailmind.ml.llm_classifier import OpenAIAdapter
    assert isinstance(client, OpenAIAdapter)
    MockClf.assert_called_once()


def test_provider_switch_auto_falls_back_to_deepseek():
    from mailmind import main
    cfg = _Cfg(openai_key="", deepseek_enabled=True)
    with patch.dict("os.environ", {"LLM_PROVIDER": "auto"}, clear=False), \
         patch("mailmind.main.DeepSeekClient") as MockDS:
        main._build_llm_client(cfg)
    MockDS.assert_called_once_with(cfg)


def test_provider_switch_openai_without_key_returns_none():
    from mailmind import main
    cfg = _Cfg(openai_key="", deepseek_enabled=True)
    with patch.dict("os.environ", {"LLM_PROVIDER": "openai"}, clear=False):
        assert main._build_llm_client(cfg) is None


def test_provider_switch_no_llm_returns_none():
    from mailmind import main
    cfg = _Cfg(openai_key="sk", deepseek_enabled=True)
    assert main._build_llm_client(cfg, no_llm=True) is None
