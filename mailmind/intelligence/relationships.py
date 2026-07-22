"""MailMind — relationship graph / contact ranking (client-strategy reframe §4.3).

Ranks contacts by reciprocity, response speed, and trust -- "who matters
most" -- learned from data this system already has (sender_profiles +
closed 'waiting_on' loops), rather than a manually-curated VIP list.
Fully deterministic: no LLM, no network. Computed fresh on read (not a
persisted nightly job) so it's never stale and needs no extra migration.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Scoring weights (documented, tunable constants -- not a learned model).
# A contact with zero signal scores exactly _BASE_SCORE (neutral, not zero).
_BASE_SCORE = 50.0
_TRUST_BONUS = {"trusted": 30.0, "neutral": 0.0, "watchlist": -30.0}
_APPROVAL_WEIGHT = 30.0  # approval_rate (0.0-1.0) * this
_VOLUME_WEIGHT = 0.5     # min(email_count, 20) * this, so max +10
_VOLUME_CAP = 20

# Reciprocity: how many days it typically takes this contact to reply, once
# the user sends them something. Faster = more reciprocal = higher score.
_RECIPROCITY_BUCKETS = (
    (1.0, 25.0),   # replies within a day
    (3.0, 15.0),   # replies within 3 days
    (7.0, 5.0),    # replies within a week
)
_RECIPROCITY_SLOW_PENALTY = -10.0  # slower than a week

# A contact at or above this score is flagged as a VIP -- surfaced first.
VIP_THRESHOLD = 70.0


def _reciprocity_component(avg_days: Optional[float]) -> float:
    if avg_days is None:
        return 0.0
    for threshold, bonus in _RECIPROCITY_BUCKETS:
        if avg_days <= threshold:
            return bonus
    return _RECIPROCITY_SLOW_PENALTY


def score_contact(profile: Dict[str, Any], reciprocity_days: Optional[float]) -> float:
    """Pure scoring function: a sender_profiles row (see
    queries.get_sender_profiles) + this contact's avg reciprocity in days
    (see queries.get_contact_reciprocity), -> a 0-100 rank score.
    """
    trust_component = _TRUST_BONUS.get(profile.get("trust_tier") or "neutral", 0.0)
    approval_rate = profile.get("approval_rate")
    approval_component = (approval_rate or 0.0) * _APPROVAL_WEIGHT
    reciprocity_component = _reciprocity_component(reciprocity_days)
    volume = min(profile.get("email_count") or 0, _VOLUME_CAP)
    volume_component = volume * _VOLUME_WEIGHT

    total = _BASE_SCORE + trust_component + approval_component + reciprocity_component + volume_component
    return round(max(0.0, min(100.0, total)), 1)


def compute_contact_rank(
    db, account: Optional[str] = None, limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return contacts ranked highest-score-first.

    Each entry: {sender_email, display_name, rank_score, vip, trust_tier,
    approval_rate, reciprocity_days, email_count}. Feeds VIP badging on
    loops (NowPage) and the Relationships panel (InsightsPage).
    """
    from ..storage.queries import get_sender_profiles, get_contact_reciprocity

    profiles = get_sender_profiles(db)
    reciprocity = get_contact_reciprocity(db, account=account)

    ranked = []
    for p in profiles:
        recip_days = reciprocity.get(p["sender_email"])
        score = score_contact(p, recip_days)
        ranked.append({
            "sender_email": p["sender_email"],
            "display_name": p.get("display_name"),
            "rank_score": score,
            "vip": score >= VIP_THRESHOLD,
            "trust_tier": p.get("trust_tier", "neutral"),
            "approval_rate": p.get("approval_rate", 0.0),
            "reciprocity_days": recip_days,
            "email_count": p.get("email_count", 0),
        })

    ranked.sort(key=lambda r: r["rank_score"], reverse=True)
    return ranked[:limit]


def get_contact_rank_map(db, account: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Same data as compute_contact_rank, keyed by sender_email for O(1)
    lookup -- used to annotate loops/queue items without an N+1 re-rank per
    item (see api/routers/now.py)."""
    return {r["sender_email"]: r for r in compute_contact_rank(db, account=account, limit=10_000)}
