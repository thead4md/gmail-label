"""Deterministic rules engine for MailMind classification.

This module provides a structured, extensible rules system for classifying emails
based on deterministic criteria. Each rule is independent, returning a match decision,
labels, score delta, and confidence.

All rules are deterministic and avoid network calls or ML logic.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from email.utils import parseaddr

from ..storage.models import Email

LOG = logging.getLogger(__name__)


@dataclass
class RuleMatch:
    """Result of evaluating a single rule against an email."""
    rule_name: str
    matched: bool
    labels: List[str] = field(default_factory=list)
    score_delta: int = 0
    confidence: float = 1.0
    reason: Optional[str] = None


@dataclass
class Rule:
    """A single classification rule."""
    name: str
    match_fn: Callable[[Email], bool]
    labels: List[str]
    score_delta: int
    confidence: float


class RulesEngine:
    """Deterministic rule-based email classifier.

    Default rules cover:
    - Finance/payment senders
    - Calendar invites
    - Newsletters (unsubscribe signals)
    - Directly addressed emails
    - Mass email penalties
    """

    def __init__(self, user_email: str = ""):
        self.rules: List[Rule] = []
        self.user_email = user_email.lower() if user_email else ""
        self._register_baseline_rules()

    def _register_baseline_rules(self) -> None:
        """Register built-in baseline rules."""
        # Finance sender domains
        self.register_rule(Rule(
            name="known_finance_sender",
            match_fn=self._match_finance_sender,
            labels=["FINANCE"],
            score_delta=10,
            confidence=0.95,
        ))

        # Calendar invite signals
        self.register_rule(Rule(
            name="calendar_invite",
            match_fn=self._match_calendar_invite,
            labels=["CALENDAR"],
            score_delta=5,
            confidence=0.85,
        ))

        # Newsletter/unsubscribe signal
        self.register_rule(Rule(
            name="newsletter_unsubscribe",
            match_fn=self._match_newsletter_unsubscribe,
            labels=["NEWSLETTER"],
            score_delta=-15,
            confidence=0.90,
        ))

        # Directly addressed (in To, not just CC/list)
        self.register_rule(Rule(
            name="directly_addressed",
            match_fn=self._match_directly_addressed,
            labels=[],
            score_delta=10,
            confidence=0.95,
        ))

        # Mass CC penalty
        self.register_rule(Rule(
            name="mass_cc_penalty",
            match_fn=self._match_mass_cc,
            labels=["MASS_EMAIL"],
            score_delta=-10,
            confidence=0.92,
        ))

    def register_rule(self, rule: Rule) -> None:
        """Register a custom rule."""
        self.rules.append(rule)
        LOG.debug(f"Registered rule: {rule.name}")

    def evaluate(self, email: Email) -> List[RuleMatch]:
        """Evaluate all rules against an email. Return list of RuleMatch results."""
        results: List[RuleMatch] = []
        for rule in self.rules:
            try:
                matched = rule.match_fn(email)
                result = RuleMatch(
                    rule_name=rule.name,
                    matched=matched,
                    labels=rule.labels if matched else [],
                    score_delta=rule.score_delta if matched else 0,
                    confidence=rule.confidence if matched else 0.0,
                )
                results.append(result)
                if matched:
                    LOG.debug(f"Rule '{rule.name}' matched email {email.gmail_id}")
            except Exception as e:
                LOG.warning(f"Rule '{rule.name}' raised exception: {e}", exc_info=True)
                results.append(RuleMatch(
                    rule_name=rule.name,
                    matched=False,
                    reason=f"Exception: {e}",
                ))
        return results

    # --- Built-in rule matchers ---

    @staticmethod
    def _match_finance_sender(email: Email) -> bool:
        """Match known payment/finance provider domains."""
        finance_domains = {
            'paypal.com',
            'stripe.com',
            'revolut.com',
            'otp.hu',
            'wise.com',
            'transferwise.com',
            'n26.com',
            'wise.com',
        }
        if not email.sender:
            return False
        sender_domain = email.sender.split('@')[-1].lower() if '@' in email.sender else ''
        return sender_domain in finance_domains

    @staticmethod
    def _match_calendar_invite(email: Email) -> bool:
        """Match calendar invite signals: MIME type or subject keywords."""
        # Check MIME types for calendar signals
        mime_types = getattr(email, 'mime_types', [])
        if any('calendar' in m.lower() or 'ics' in m.lower() for m in (mime_types or [])):
            return True
        # Check subject for keywords
        subject = (email.subject or '').lower()
        calendar_keywords = [
            'invitation',
            'invite',
            'meeting',
            'event',
            'calendar',
        ]
        return any(kw in subject for kw in calendar_keywords)

    @staticmethod
    def _match_newsletter_unsubscribe(email: Email) -> bool:
        """Match newsletter/unsubscribe signals in body text."""
        body = (email.body_text or '').lower()
        # Look for unsubscribe link patterns
        unsubscribe_patterns = [
            r'unsubscribe',
            r'list-unsubscribe',
            r'manage\s+subscriptions?',
            r'stop\s+receiving',
            r'click\s+here.*unsubscribe',
        ]
        return any(re.search(pattern, body) for pattern in unsubscribe_patterns)

    def _match_directly_addressed(self, email: Email) -> bool:
        """Match emails where user_email is a recipient (directly addressed).

        Normalizes each recipient string by extracting the email address
        (handles "Display Name <email@domain.com>" format) and compares
        against user_email case-insensitively.

        Heuristics reject likely mailing-list / announcement / alias traffic.
        """
        if not self.user_email:
            return False

        # 1. Normalize recipients and check if user_email is a recipient
        recipients = email.recipients or []
        is_recipient = any(
            self.user_email in parseaddr(r)[1].lower()
            for r in recipients
        )
        if not is_recipient:
            return False

        # 2. Reject list-style sender prefixes (very strong signal it's NOT direct)
        sender = (email.sender or '').lower()
        list_sender_prefixes = [
            'noreply@',
            'no-reply@',
            'no_reply@',
            'list@',
            'bounce@',
            'postmaster@',
            'mailer-daemon@',
            'mail-daemon@',
            'svc_',  # Service account patterns
            'notifications@',  # Notification service
            'automated@',
            'system@'
        ]
        if any(sender.startswith(prefix) for prefix in list_sender_prefixes):
            return False

        # Check for bulk email patterns in sender local part
        sender_local = sender.split('@')[0] if '@' in sender else sender
        bulk_patterns = ['noreply', 'no-reply', 'list', 'bounce', 'mailer', 'notification', 'alert', 'report']
        if any(pattern in sender_local for pattern in bulk_patterns):
            return False

        # 3. Reject if there are many To: recipients (indicates list or broad send)
        if len(recipients) > 3:  # More than 3 direct recipients is unusual for true 1:1
            return False

        # 4. Reject if unsubscribe/list signals present
        body = (email.body_text or '').lower()
        list_signals = [
            'unsubscribe',
            'list-unsubscribe',
            'manage subscriptions',
            'stop receiving',
            'list-id',
            'mailing list',
            'reply to this email',  # Generic mass email signal
        ]
        if any(signal in body for signal in list_signals):
            return False

        # 5. Detect mass-send headers if available (raw headers would help here)
        # For now, use subject heuristics
        subject = (email.subject or '').lower()
        mass_subject_patterns = [
            '[list]', '[announce]', '[bulk]',
            'you were mentioned',  # Notification-style
            'your account',  # Auto-notification
        ]
        if any(pattern in subject for pattern in mass_subject_patterns):
            return False

        # 6. If sender has no domain, likely an internal/system address
        if '@' not in sender and sender:
            return False

        # Passed all rejection filters: this appears to be a direct email to the user
        return True

    @staticmethod
    def _match_mass_cc(email: Email) -> bool:
        """Penalize emails with mass CC (many recipients)."""
        recipients = email.recipients or []
        cc_addrs = getattr(email, 'cc_addresses', []) or []
        all_recipients = recipients + cc_addrs
        # More than 5 recipients is considered mass email
        return len(all_recipients) > 5

