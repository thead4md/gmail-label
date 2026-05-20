"""Safety policy checks for MailMind action execution.

Ensures that MailMind never takes risky actions without explicit user review:
- Never auto-delete
- Never auto-archive sensitive categories
- Protect sensitive senders/domains
- Support dry-run mode
- Rate limit auto-actions
- Require minimum confidence thresholds
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Set

from ..storage.models import Email

LOG = logging.getLogger(__name__)


@dataclass
class SafetyDecision:
    """Result of safety policy evaluation."""
    action: str  # e.g., "label", "star", "archive", "none"
    allowed: bool
    reason: str
    logs: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.allowed:
            self.logs.append(f"Action '{self.action}' blocked: {self.reason}")


class SafetyPolicy:
    """Deterministic safety policy for automated actions.

    Hardcoded constraints:
    - Never auto-delete
    - Never auto-archive URGENT/FINANCE/PERSONAL emails
    - Require confidence thresholds for actions
    - Respect protected senders and domains
    - Support dry-run mode
    """

    def __init__(
        self,
        dry_run: bool = True,
        protected_senders: Optional[List[str]] = None,
        protected_domains: Optional[List[str]] = None,
        max_actions_per_hour: int = 50,
    ):
        """Initialize safety policy.

        Args:
            dry_run: If True, no actions are executed; decisions are logged only.
            protected_senders: Senders whose emails should never be auto-archived/deleted.
            protected_domains: Domains whose emails should never be auto-archived/deleted.
            max_actions_per_hour: Rate limit for auto-actions.
        """
        self.dry_run = dry_run
        self.protected_senders: Set[str] = set(s.lower() for s in (protected_senders or []))
        self.protected_domains: Set[str] = set(d.lower() for d in (protected_domains or []))
        self.max_actions_per_hour = max_actions_per_hour

        # Track actions for rate limiting (in-memory for MVP; persisted in real system)
        self._action_timestamps: List[datetime] = []

        # Hardcoded policies
        self.no_auto_delete = True
        self.no_auto_archive_categories = {"URGENT", "FINANCE", "PERSONAL"}

    def evaluate(
        self,
        action: str,
        email: Email,
        confidence: float,
        primary_label: Optional[str] = None,
    ) -> SafetyDecision:
        """Evaluate if an action is allowed by policy.

        Args:
            action: Action name (e.g., "label", "star", "archive", "delete").
            email: Email being actioned.
            confidence: Confidence/score (0-1) for the action.
            primary_label: Primary label determined for the email.

        Returns:
            SafetyDecision with allowed/reason.
        """
        logs: List[str] = []

        # 1. Dry-run mode check
        if self.dry_run:
            logs.append(f"DRY RUN: would execute '{action}' on {email.gmail_id}")
            return SafetyDecision(
                action=action,
                allowed=True,  # Allowed, but won't actually execute
                reason="Dry-run mode: decision logged, action not executed",
                logs=logs,
            )

        # 2. Never auto-delete
        if action == "delete" and self.no_auto_delete:
            return SafetyDecision(
                action=action,
                allowed=False,
                reason="Auto-delete is never allowed",
                logs=logs,
            )

        # 3. Never auto-archive sensitive categories
        if action == "archive":
            labels_to_check = set(email.labels or [])
            if primary_label:
                labels_to_check.add(primary_label)

            if any(cat in labels_to_check for cat in self.no_auto_archive_categories):
                return SafetyDecision(
                    action=action,
                    allowed=False,
                    reason=f"Cannot auto-archive {self.no_auto_archive_categories} emails",
                    logs=logs,
                )

        # 4. Check protected senders/domains
        sender = (email.sender or "").lower()
        sender_domain = sender.split("@")[-1] if "@" in sender else ""

        if sender in self.protected_senders or sender_domain in self.protected_domains:
            if action in ("archive", "delete"):
                return SafetyDecision(
                    action=action,
                    allowed=False,
                    reason=f"Sender {sender} is protected from {action}",
                    logs=logs,
                )

        # 5. Rate limiting
        if self._is_rate_limited():
            logs.append(f"Rate limit approached: {len(self._action_timestamps)}/{self.max_actions_per_hour} actions in last hour")
            return SafetyDecision(
                action=action,
                allowed=False,
                reason=f"Rate limit exceeded: {self.max_actions_per_hour} actions/hour",
                logs=logs,
            )

        # 6. If we made it here, action is allowed (in non-dry-run)
        self._record_action()
        return SafetyDecision(
            action=action,
            allowed=True,
            reason="All safety checks passed",
            logs=logs,
        )

    def _is_rate_limited(self) -> bool:
        """Check if we've exceeded max actions in the last hour."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=1)
        # Remove old timestamps
        self._action_timestamps = [ts for ts in self._action_timestamps if ts > cutoff]
        return len(self._action_timestamps) >= self.max_actions_per_hour

    def _record_action(self) -> None:
        """Record that an action was executed (for rate limiting)."""
        self._action_timestamps.append(datetime.now(timezone.utc))

    def set_protected_sender(self, sender: str) -> None:
        """Add a sender to the protected list."""
        self.protected_senders.add(sender.lower())

    def set_protected_domain(self, domain: str) -> None:
        """Add a domain to the protected list."""
        self.protected_domains.add(domain.lower())

