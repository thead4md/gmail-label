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
from dataclasses import asdict, dataclass, field
from typing import Optional, Dict, List
from datetime import datetime, timezone

from ..storage.models import Email, SenderReputation
from .rules import RuleMatch
from ..intelligence.sender_memory import get_sender_trust_tier
from ..storage.database import Database
from ..taxonomy import base_score, DEFAULT_BASE_SCORE, is_known

LOG = logging.getLogger(__name__)


@dataclass
class ScoreResult:
    """Detailed scoring breakdown for debugging and display."""
    total_score: int  # Final score 0-100
    base_score: int
    rule_contribution: int
    direct_mention_bonus: int
    recency_bonus: int
    sender_trust: int
    label_priority_weight: int = 0
    penalties: Dict[str, int] = field(default_factory=dict)
    primary_label: Optional[str] = None
    breakdown_text: str = ""

    def to_dict(self) -> Dict:
        """Convert to dict for logging/storage."""
        return asdict(self)


class PriorityScorer:
    """Deterministic priority scoring engine."""

    def __init__(self, user_email: str = "", recency_hours: int = 24):
        """Initialize scorer.

        Args:
            user_email: The user's primary email address (for direct mention bonus).
            recency_hours: Hours threshold for recency bonus.
        """
        self.user_email = user_email
        self.recency_hours = recency_hours

    def compute_score(
        self,
        email: Email,
        rule_matches: List[RuleMatch],
        sender_reputation: Optional[SenderReputation] = None,
        db: Optional[Database] = None,
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
        if primary_label and not is_known(primary_label):
            LOG.warning("Unknown label %r — scoring with default", primary_label)
        base = base_score(primary_label)

        # 2. Rule contributions (sum of matched rule deltas weighted by confidence)
        rule_contribution = 0
        matched_rule_names = []
        for match in rule_matches:
            if match.matched:
                contribution = int(match.score_delta * match.confidence)
                rule_contribution += contribution
                matched_rule_names.append(f"{match.rule_name}({contribution})")

        # 3. Direct mention bonus
        direct_mention_bonus = self._compute_direct_mention_bonus(email)

        # 4. Recency bonus
        recency_bonus = self._compute_recency_bonus(email)

        # 5. Sender trust score
        sender_trust = self._compute_sender_trust(email, sender_reputation)

        # 5b. Sender memory nudges (modest, deterministic)
        # Uses sender_profiles table when db is provided. Does not change thresholds.
        memory_nudge = 0
        try:
            if db and email.sender:
                tier = get_sender_trust_tier(db, email.sender)
                if tier == 'trusted':
                    memory_nudge = 5
                elif tier == 'watchlist':
                    memory_nudge = -8
        except Exception:
            # Fail-safe: do not crash scoring if sender memory unavailable
            memory_nudge = 0

        # Add memory nudge into sender_trust aggregation
        sender_trust = int(sender_trust) + int(memory_nudge)

        # 5c. Label priority weight
        label_priority_weight = 0
        try:
            if db and primary_label:
                priorities = {}
                from ..storage.database import Database
                if isinstance(db, Database):
                    priorities = db.get_label_priorities()
                label_priority_weight = priorities.get(primary_label, 0)
        except Exception:
            # Fail-safe: do not crash scoring if label priorities unavailable
            label_priority_weight = 0

        # 6. Penalties accumulate
        penalties = {}

        # Newsletter penalty
        if primary_label == "NEWSLETTER" or any(m.rule_name == "newsletter_unsubscribe" and m.matched for m in rule_matches):
            penalties["newsletter_penalty"] = -20

        # Mass email penalty
        if primary_label == "MASS_EMAIL" or any(m.rule_name == "mass_cc_penalty" and m.matched for m in rule_matches):
            penalties["mass_email_penalty"] = -5

        total_penalties = sum(penalties.values())

        # 7. Clamp to 0-100
        score_before_clamp = base + rule_contribution + direct_mention_bonus + recency_bonus + sender_trust + label_priority_weight + total_penalties
        total_score = max(0, min(100, score_before_clamp))

        # 8. Generate breakdown text
        breakdown = self._build_breakdown_text(
            primary_label, base, rule_contribution, direct_mention_bonus, recency_bonus,
            sender_trust, label_priority_weight, penalties, matched_rule_names, score_before_clamp, total_score
        )

        result = ScoreResult(
            total_score=total_score,
            base_score=base,
            rule_contribution=rule_contribution,
            direct_mention_bonus=direct_mention_bonus,
            recency_bonus=recency_bonus,
            sender_trust=sender_trust,
            label_priority_weight=label_priority_weight,
            penalties=penalties,
            primary_label=primary_label,
            breakdown_text=breakdown,
        )
        LOG.debug(f"Scored email {email.gmail_id}: {total_score} (primary_label={primary_label})")
        return result

    def _compute_direct_mention_bonus(self, email: Email) -> int:
        """Give a bonus if the user's email is in the recipients."""
        if self.user_email and email.recipients:
            if any(self.user_email.lower() in r.lower() for r in email.recipients):
                return 30
        return 0

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

    def _compute_recency_bonus(self, email: Email) -> int:
        """Give a bonus to recent emails (within recency_hours)."""
        if not email.date_ts:
            return 0
        now_ts = int(datetime.now(timezone.utc).timestamp())
        age_seconds = now_ts - email.date_ts
        age_hours = age_seconds / 3600.0
        # Recency bonus: +5 if within recency_hours, linearly decay after
        if age_hours <= self.recency_hours:
            return 5
        elif age_hours <= self.recency_hours * 2:
            return max(0, int(5 * (2 - age_hours / self.recency_hours)))
        return 0

    @staticmethod
    def _compute_sender_trust(email: Email, sender_rep: Optional[SenderReputation]) -> int:
        """Compute sender trust score (0-10 boost range)."""
        if not sender_rep:
            return 0
        # If sender has high trust, boost (clamped 0-10)
        if sender_rep.score:
            return min(10, max(0, int(sender_rep.score)))
        return 0

    @staticmethod
    def _build_breakdown_text(
        primary_label: str,
        base_score: int,
        rule_contribution: int,
        direct_mention_bonus: int,
        recency_bonus: int,
        sender_trust: int,
        label_priority_weight: int,
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
            f"Direct mention bonus: {direct_mention_bonus}",
            f"Recency bonus: {recency_bonus}",
            f"Sender trust: {sender_trust}",
        ]
        if label_priority_weight != 0:
            lines.append(f"Label priority weight: {label_priority_weight}")
        if penalties:
            penalty_str = ", ".join(f"{k}={v}" for k, v in penalties.items())
            lines.append(f"Penalties: {penalty_str}")
        lines.append(f"Score before clamp: {score_before_clamp}")
        lines.append(f"Final score (clamped 0-100): {final_score}")
        return "\n".join(lines)

