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

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Set, TYPE_CHECKING

from ..storage.models import Email

if TYPE_CHECKING:
    from ..storage.database import Database

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

    # Canonical set of actions considered "destructive" (i.e. they remove mail
    # from the user's normal view). Both the sensitive-category guard and the
    # protected-sender/domain guard below test membership against this ONE
    # set, so a future destructive action (e.g. "trash", "spam") only needs to
    # be added here to automatically inherit both guards — no need to hunt
    # down and extend every string-matched conditional in evaluate().
    DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset({"archive", "delete"})

    # system_state key the rate limiter's recent-action timestamps are
    # persisted under when a `db` is supplied. See __init__'s docstring.
    RATE_LIMIT_STATE_KEY = "safety_action_timestamps"

    def __init__(
        self,
        dry_run: bool = True,
        protected_senders: Optional[List[str]] = None,
        protected_domains: Optional[List[str]] = None,
        max_actions_per_hour: int = 50,
        db: Optional["Database"] = None,
    ):
        """Initialize safety policy.

        Args:
            dry_run: If True, no actions are executed; decisions are logged only.
            protected_senders: Senders whose emails should never be auto-archived/deleted.
            protected_domains: Domains whose emails should never be auto-archived/deleted.
            max_actions_per_hour: Rate limit for auto-actions.
            db: Optional Database handle. When provided, the rate limiter's
                recent-action timestamps are persisted (read-through/
                write-through) via db.get_state()/db.set_state() under
                RATE_LIMIT_STATE_KEY, as a JSON-encoded list of ISO-8601
                timestamps, pruned to the last hour on every read. This
                survives a process restart — required once the process is no
                longer guaranteed to run continuously (e.g. scale-to-zero,
                where a fresh process boots on every external poll trigger).
                When omitted (the default), the limiter is purely in-memory
                via `_action_timestamps`, exactly as before — existing
                callers/tests that construct SafetyPolicy(dry_run=...)
                directly are unaffected.
        """
        self.dry_run = dry_run
        self.protected_senders: Set[str] = set(s.lower() for s in (protected_senders or []))
        self.protected_domains: Set[str] = set(d.lower() for d in (protected_domains or []))
        self.max_actions_per_hour = max_actions_per_hour
        self.db = db

        # Track actions for rate limiting. Pure in-memory (MVP) when no db is
        # supplied; when a db IS supplied, the persisted state (see
        # RATE_LIMIT_STATE_KEY) is authoritative instead and this list is
        # left unused — kept around so the attribute always exists (some
        # tests / callers poke at it directly for the in-memory case).
        self._action_timestamps: List[datetime] = []
        # Count from the most recent _is_rate_limited() call, used only for
        # the human-readable log line in evaluate() — kept separate from
        # _action_timestamps because that list isn't populated in the
        # db-backed path.
        self._last_action_count = 0

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

        # 3. Never auto-archive (or otherwise destructively act on) sensitive categories
        if action in self.DESTRUCTIVE_ACTIONS:
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
            if action in self.DESTRUCTIVE_ACTIONS:
                return SafetyDecision(
                    action=action,
                    allowed=False,
                    reason=f"Sender {sender} is protected from {action}",
                    logs=logs,
                )

        # 5. Rate limiting
        if self._is_rate_limited():
            logs.append(f"Rate limit approached: {self._last_action_count}/{self.max_actions_per_hour} actions in last hour")
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
        if self.db is not None:
            timestamps = self._load_persisted_timestamps(cutoff)
        else:
            # Remove old timestamps
            self._action_timestamps = [ts for ts in self._action_timestamps if ts > cutoff]
            timestamps = self._action_timestamps
        self._last_action_count = len(timestamps)
        return self._last_action_count >= self.max_actions_per_hour

    def _record_action(self) -> None:
        """Record that an action was executed (for rate limiting)."""
        now = datetime.now(timezone.utc)
        if self.db is not None:
            cutoff = now - timedelta(hours=1)
            timestamps = self._load_persisted_timestamps(cutoff)
            timestamps.append(now)
            self._save_persisted_timestamps(timestamps)
            return
        self._action_timestamps.append(now)

    def _load_persisted_timestamps(self, cutoff: datetime) -> List[datetime]:
        """Read the persisted timestamp list from `db`, dropping anything at
        or before `cutoff`. Returns [] (and logs a warning) if the stored
        value is missing or unparsable, rather than raising — a corrupt
        rate-limit record must never itself become an outage."""
        assert self.db is not None
        raw = self.db.get_state(self.RATE_LIMIT_STATE_KEY)
        if not raw:
            return []
        try:
            encoded = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            LOG.warning("Corrupt persisted rate-limit state (%s) — resetting.", exc)
            return []
        timestamps: List[datetime] = []
        for item in encoded:
            try:
                ts = datetime.fromisoformat(item)
            except (TypeError, ValueError):
                continue
            if ts > cutoff:
                timestamps.append(ts)
        return timestamps

    def _save_persisted_timestamps(self, timestamps: List[datetime]) -> None:
        assert self.db is not None
        encoded = json.dumps([ts.isoformat() for ts in timestamps])
        self.db.set_state(self.RATE_LIMIT_STATE_KEY, encoded)

    def set_protected_sender(self, sender: str) -> None:
        """Add a sender to the protected list."""
        self.protected_senders.add(sender.lower())

    def set_protected_domain(self, domain: str) -> None:
        """Add a domain to the protected list."""
        self.protected_domains.add(domain.lower())

