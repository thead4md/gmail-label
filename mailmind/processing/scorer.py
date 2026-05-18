"""Priority scoring for MailMind emails.

Combines multiple signals into a deterministic 0-100 priority score:
- Label base score
- Rule contributions
- Recency bonus
- Sender trust score
- Penalties (newsletter, mass email, etc.)

All scoring is deterministic and local-only.
"""
from __future__ import annotations

import logging
import json
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from datetime import datetime

from ..storage.models import Email, SenderReputation
from .rules import RuleMatch

LOG = logging.getLogger(__name__)


# Label base scores (0-100 scale)
LABEL_BASE_SCORES = {
    "URGENT": 80,
    "WORK": 60,
    "FINANCE": 55,
    "PERSONAL": 50,
    "NOTIFICATION": 30,
    "DEFER": 20,
    "NEWSLETTER": 10,
    "SPAMCANDIDATE": 5,
}
DEFAULT_BASE_SCORE = 30


@dataclass
class ScoreResult:
    """Detailed scoring breakdown for debugging and display."""
    total_score: int  # Final score 0-100
    base_score: int
    rule_contribution: int
    recency_bonus: int
    sender_trust: int
    penalties: Dict[str, int] = field(default_factory=dict)
    primary_label: Optional[str] = None
    breakdown_text: str = ""

    def to_dict(self) -> Dict:
        """Convert to dict for logging/storage."""
        return {
            "total_score": self.total_score,
            "base_score": self.base_score,
            "rule_contribution": self.rule_contribution,
            "recency_bonus": self.recency_bonus,
            "sender_trust": self.sender_trust,
            "penalties": self.penalties,
            "primary_label": self.primary_label,
        }


class PriorityScorer:
    """Deterministic priority scoring engine."""

    def __init__(self, recency_hours: int = 24):
        """Initialize scorer.

        Args:
            recency_hours: Hours threshold for recency bonus.
        """
        self.recency_hours = recency_hours

    def compute_score(
        self,
        email: Email,
        rule_matches: List[RuleMatch],
        sender_reputation: Optional[SenderReputation] = None,
    ) -> ScoreResult:
        """Compute priority score for an email.

        Args:
            email: Normalized email model.
            rule_matches: List of evaluated rule match results.
            sender_reputation: Optional sender reputation data.

        Returns:
            ScoreResult with detailed breakdown.
        """
        # 1. Determine primary label and base score
        primary_label = self._determine_primary_label(email, rule_matches)
        base_score = LABEL_BASE_SCORES.get(primary_label, DEFAULT_BASE_SCORE)

        # 2. Rule contributions (sum of matched rule deltas weighted by confidence)
        rule_contribution = 0
        matched_rule_names = []
        for match in rule_matches:
            if match.matched:
                contribution = int(match.score_delta * match.confidence)
                rule_contribution += contribution
                matched_rule_names.append(f"{match.rule_name}({contribution})")

        # 3. Recency bonus
        recency_bonus = self._compute_recency_bonus(email)

        # 4. Sender trust score
        sender_trust = self._compute_sender_trust(email, sender_reputation)

        # 5. Penalties accumulate
        penalties = {}

        # Newsletter penalty
        if primary_label == "NEWSLETTER" or any(m.rule_name == "newsletter_unsubscribe" and m.matched for m in rule_matches):
            penalties["newsletter_penalty"] = -20

        # Mass email penalty
        if primary_label == "MASS_EMAIL" or any(m.rule_name == "mass_cc_penalty" and m.matched for m in rule_matches):
            penalties["mass_email_penalty"] = -5

        total_penalties = sum(penalties.values())

        # 6. Clamp to 0-100
        score_before_clamp = base_score + rule_contribution + recency_bonus + sender_trust + total_penalties
        total_score = max(0, min(100, score_before_clamp))

        # 7. Generate breakdown text
        breakdown = self._build_breakdown_text(
            primary_label, base_score, rule_contribution, recency_bonus,
            sender_trust, penalties, matched_rule_names, score_before_clamp, total_score
        )

        result = ScoreResult(
            total_score=total_score,
            base_score=base_score,
            rule_contribution=rule_contribution,
            recency_bonus=recency_bonus,
            sender_trust=sender_trust,
            penalties=penalties,
            primary_label=primary_label,
            breakdown_text=breakdown,
        )
        LOG.debug(f"Scored email {email.gmail_id}: {total_score} (primary_label={primary_label})")
        return result

    @staticmethod
    def _determine_primary_label(email: Email, rule_matches: List[RuleMatch]) -> str:
        """Determine the primary label for the email from labels and rules."""
        # Collect all labels from matched rules
        all_labels = set(email.labels or [])
        for match in rule_matches:
            if match.matched and match.labels:
                all_labels.update(match.labels)

        # Prioritize by importance
        priority_order = [
            "URGENT", "WORK", "FINANCE", "PERSONAL",
            "CALENDAR", "NOTIFICATION",
            "NEWSLETTER", "SPAMCANDIDATE",
            "MASS_EMAIL", "DEFER",
        ]
        for label in priority_order:
            if label in all_labels:
                return label

        # If email is in UNREAD, INBOX, etc., return a reasonable default
        if "UNREAD" in all_labels or "INBOX" in all_labels:
            return "NOTIFICATION"

        return "NOTIFICATION"  # Safe default

    @staticmethod
    def _compute_recency_bonus(email: Email) -> int:
        """Give a bonus to recent emails (within recency_hours)."""
        if not email.date_ts:
            return 0
        now_ts = int(datetime.utcnow().timestamp())
        age_seconds = now_ts - email.date_ts
        age_hours = age_seconds / 3600.0
        # Recency bonus: +5 if within 24 hours, linearly decay to 0 after 48 hours
        if age_hours <= 24:
            return 5
        elif age_hours <= 48:
            return max(0, int(5 * (2 - age_hours / 24)))
        return 0

    @staticmethod
    def _compute_sender_trust(email: Email, sender_rep: Optional[SenderReputation]) -> int:
        """Compute sender trust score (0-10 boost range)."""
        if not sender_rep:
            return 0
        # If sender has high trust, boost (clamped 0-10)
        if hasattr(sender_rep, 'trust_score') and sender_rep.trust_score:
            return min(10, max(0, int(sender_rep.trust_score)))
        return 0

    @staticmethod
    def _build_breakdown_text(
        primary_label: str,
        base_score: int,
        rule_contribution: int,
        recency_bonus: int,
        sender_trust: int,
        penalties: Dict[str, int],
        matched_rule_names: List[str],
        score_before_clamp: int,
        final_score: int,
    ) -> str:
        """Build human-readable scoring breakdown."""
        lines = [
            f"Primary label: {primary_label}",
            f"Base score: {base_score}",
            f"Rule contribution: {rule_contribution} (from: {', '.join(matched_rule_names) or 'none'})",
            f"Recency bonus: {recency_bonus}",
            f"Sender trust: {sender_trust}",
        ]
        if penalties:
            penalty_str = ", ".join(f"{k}={v}" for k, v in penalties.items())
            lines.append(f"Penalties: {penalty_str}")
        lines.append(f"Score before clamp: {score_before_clamp}")
        lines.append(f"Final score (clamped 0-100): {final_score}")
        return "\n".join(lines)

