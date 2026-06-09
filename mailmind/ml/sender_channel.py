"""Sender channel for the 80/20 content/sender blend.

Builds P_sender — a label probability distribution derived from:
  1. The learned per-sender prior (from user corrections via DB).
  2. Known finance-domain signal (hardcoded high-confidence boost).

Returns {} to signal "abstain" when there is not enough evidence,
so the blend falls back to pure content for that sender.
"""
from __future__ import annotations

import logging
import re
from typing import Optional, TYPE_CHECKING

from ..intelligence.patterns import FINANCE_DOMAINS

if TYPE_CHECKING:
    from ..storage.database import Database

LOG = logging.getLogger(__name__)

_FINANCE_CONFIDENCE = 0.85  # probability mass placed on FINANCE for known domains


def _extract_domain(sender: str) -> str:
    """Return the domain part of an email address, lower-cased."""
    low = (sender or "").lower().strip(" <>")
    if "@" in low:
        return low.split("@", 1)[1].strip()
    return ""


def build_sender_distribution(
    sender: str,
    db: Optional["Database"] = None,
    account: Optional[str] = None,
    min_count: int = 3,
) -> dict:
    """Return a normalised label distribution for sender.

    Decision logic (highest precedence first):
      1. Learned prior from DB (user corrections) — if enough observations.
      2. Finance-domain hardcoded signal.
      3. Abstain ({}) — pure content gets 100% weight for this email.

    Args:
        sender: The raw sender email address.
        db: Optional Database for learned prior lookup.
        account: Mailbox account for account-scoped lookups.
        min_count: Minimum confirmed observations before the prior is trusted.

    Returns:
        dict mapping label → probability (sums to ~1.0), or {} to abstain.
    """
    if not sender:
        return {}

    # 1. Learned prior (highest quality signal)
    if db is not None:
        from ..storage.queries import get_sender_label_prior
        prior = get_sender_label_prior(db, sender, account=account, min_count=min_count)
        if prior:
            LOG.debug("Sender prior for %s: %s", sender, prior)
            return prior

    # 2. Finance-domain hardcoded signal
    domain = _extract_domain(sender)
    if domain and domain in FINANCE_DOMAINS:
        LOG.debug("Finance domain signal for %s", sender)
        other_mass = 1.0 - _FINANCE_CONFIDENCE
        return {"FINANCE": _FINANCE_CONFIDENCE, "OTHER": other_mass}

    # 3. Abstain
    return {}


def blend_distributions(
    p_content: dict,
    p_sender: dict,
    content_weight: float = 0.80,
    sender_weight: float = 0.20,
) -> dict:
    """Blend two label probability distributions with given weights.

    If p_sender is empty (abstain), returns p_content unchanged so content
    gets 100% weight for that email. Otherwise returns the normalised weighted
    sum across the union of all labels in both distributions.

    Args:
        p_content: Content-channel distribution {label: prob}.
        p_sender: Sender-channel distribution {label: prob}, or {} to abstain.
        content_weight: Weight for the content channel (default 0.80).
        sender_weight: Weight for the sender channel (default 0.20).

    Returns:
        Blended {label: prob} distribution normalised to sum ≈ 1.0.
    """
    # Bug #6 fix: validate that weights sum to ~1.0; warn and re-normalise if not.
    _weight_sum = content_weight + sender_weight
    if abs(_weight_sum - 1.0) > 1e-4:
        LOG.warning(
            "blend_distributions: weights do not sum to 1.0 "
            "(content=%.4f sender=%.4f sum=%.4f). Re-normalising.",
            content_weight, sender_weight, _weight_sum,
        )
        if _weight_sum > 0:
            content_weight = content_weight / _weight_sum
            sender_weight = sender_weight / _weight_sum

    if not p_sender:
        return p_content

    all_labels = set(p_content) | set(p_sender)
    blended = {}
    for lbl in all_labels:
        blended[lbl] = (
            content_weight * p_content.get(lbl, 0.0)
            + sender_weight * p_sender.get(lbl, 0.0)
        )

    total = sum(blended.values())
    if total <= 0:
        return p_content

    return {lbl: v / total for lbl, v in blended.items()}
