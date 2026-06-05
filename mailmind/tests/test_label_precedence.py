"""Truth table for resolve_label_precedence — the single source of label precedence.

This function is where "silent override" bugs historically appeared, so its full
precedence is pinned here independently of the pipeline plumbing.
"""
from __future__ import annotations

from mailmind.processing.pipeline import resolve_label_precedence

OVERRIDE = 0.90   # llm_confidence_override
FLOOR = 0.65      # LLM_FALLBACK_FLOOR


def _resolve(**kw):
    base = dict(
        base_label="NOTIFICATION",
        ml_confident=False,
        llm_present=False,
        llm_label=None,
        llm_confidence=None,
        llm_override_floor=OVERRIDE,
        llm_fallback_floor=FLOOR,
        routing_source=None,
        routing_label=None,
    )
    base.update(kw)
    return resolve_label_precedence(base.pop("base_label"), **base)


def test_rules_only():
    assert _resolve() == ("NOTIFICATION", "rules", "rules")


def test_ml_confident_marks_hybrid_but_not_primary():
    # ML confidence flows via routing_result in production; here it only flips
    # pipeline_used to hybrid without changing the label.
    assert _resolve(ml_confident=True) == ("NOTIFICATION", "rules", "hybrid")


def test_confident_llm_no_router_overrides():
    # Case 4: no router, LLM >= override -> LLM label wins (source stays 'rules').
    assert _resolve(llm_present=True, llm_label="FINANCE", llm_confidence=0.95) == (
        "FINANCE", "rules", "hybrid",
    )


def test_midconfidence_llm_no_router_does_not_override():
    # 0.85 is below the 0.90 override and there's no fallback router to trigger
    # the lower floor -> base label kept.
    assert _resolve(llm_present=True, llm_label="FINANCE", llm_confidence=0.85) == (
        "NOTIFICATION", "rules", "hybrid",
    )


def test_router_real_tier_wins_over_llm():
    # Case 1: a real router tier ('rules') beats even a confident LLM.
    assert _resolve(
        llm_present=True, llm_label="FINANCE", llm_confidence=0.95,
        routing_source="rules", routing_label="NEWSLETTER",
    ) == ("NEWSLETTER", "rules", "rules")


def test_router_ml_tier_wins():
    assert _resolve(routing_source="ml", routing_label="CALENDAR") == (
        "CALENDAR", "ml", "ml",
    )


def test_fallback_with_usable_llm_picks_llm():
    # Case 2: fallback + LLM at/above the floor (0.85 >= 0.65) -> LLM wins.
    assert _resolve(
        llm_present=True, llm_label="PERSONAL", llm_confidence=0.85,
        routing_source="fallback", routing_label="NOTIFICATION",
    ) == ("PERSONAL", "llm", "hybrid")


def test_fallback_with_unusable_llm_keeps_fallback():
    # Case 3: fallback + LLM below the floor -> the router fallback label.
    assert _resolve(
        llm_present=True, llm_label="PERSONAL", llm_confidence=0.50,
        routing_source="fallback", routing_label="NEWSLETTER",
    ) == ("NEWSLETTER", "fallback", "fallback")


def test_fallback_no_llm_keeps_fallback():
    assert _resolve(routing_source="fallback", routing_label="NEWSLETTER") == (
        "NEWSLETTER", "fallback", "fallback",
    )
