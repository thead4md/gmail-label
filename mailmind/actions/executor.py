"""Safe Gmail action executor for MailMind.

Executes actions on Gmail messages with confidence thresholds and safety policy checks:
- Apply labels
- Star message
- Mark important
- Archive message
- Never delete (by design)

All Gmail API calls are isolated in this module. Actions respect dry-run mode and
safety policy decisions strictly.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from googleapiclient.errors import HttpError

from ..storage.models import Email, ActionApplied
from ..storage.database import Database
from .safety import SafetyPolicy, SafetyDecision
from ..processing.scorer import ScoreResult

LOG = logging.getLogger(__name__)


# Confidence thresholds for automatic action execution
CONFIDENCE_THRESHOLDS = {
    "label": 0.65,
    "star": 0.75,
    "mark_important": 0.75,
    "archive": 0.85,
    "delete": 1.00,  # Never execute (would require 1.0, which is unreachable due to uncertainty)
}


class ActionExecutor:
    """Executes safe actions on Gmail messages.

    Integrates with safety policy, confidence thresholds, and persistence layer.
    Respects dry-run mode strictly: no Gmail API calls are made in dry-run.
    """

    def __init__(
        self,
        service,
        db: Database,
        safety_policy: SafetyPolicy,
        user_id: str = "me",
        rate_limit_seconds: float = 0.3,
    ):
        """Initialize action executor.

        Args:
            service: Gmail API service object.
            db: Database instance for logging actions.
            safety_policy: SafetyPolicy instance for permission checks.
            user_id: Gmail user ID (default: 'me').
            rate_limit_seconds: Rate limit between Gmail API calls.
        """
        self.service = service
        self.db = db
        self.safety_policy = safety_policy
        self.user_id = user_id
        self.rate_limit_seconds = rate_limit_seconds

    def suggest_action(
        self,
        email: Email,
        score: ScoreResult,
    ) -> Optional[str]:
        """Suggest an action based on priority score and primary label.

        total_score is the *priority* score (0-100), not classification
        confidence. Never compare total_score/100 against CONFIDENCE_THRESHOLDS
        — those thresholds are for classification confidence (0-1 from the LLM),
        which lives on Prediction.confidence, not on ScoreResult.
        """
        primary_label = score.primary_label or "NOTIFICATION"
        total_score = score.total_score

        if total_score >= 80:
            return "star"
        elif total_score >= 60:
            return "mark_important"
        elif primary_label in ("NEWSLETTER", "MASS_EMAIL"):
            # Newsletters/mass-mail are correctly classified; archive regardless
            # of priority score (which is intentionally suppressed for bulk mail).
            return "archive"
        else:
            # Any other classified email is worth labeling.
            return "label"

    def execute_action(
        self,
        email: Email,
        action: str,
        score: ScoreResult,
    ) -> bool:
        """Execute an action on an email if safety policy permits.

        Args:
            email: Email to action.
            action: Action name (e.g., "label", "star", "archive").
            score: ScoreResult containing confidence/score.

        Returns:
            True if action was executed (or dry-run logged), False if blocked.
        """
        confidence = score.total_score / 100.0
        primary_label = score.primary_label or "NOTIFICATION"

        # Check safety policy
        safety_decision = self.safety_policy.evaluate(
            action=action,
            email=email,
            confidence=confidence,
            primary_label=primary_label,
        )

        if not safety_decision.allowed:
            LOG.info(f"Action '{action}' on {email.gmail_id} blocked: {safety_decision.reason}")
            # Log the decision even if blocked
            self._log_action_attempted(email, action, "blocked", safety_decision)
            return False

        # Check confidence threshold
        if confidence < CONFIDENCE_THRESHOLDS.get(action, 0.65):
            LOG.info(f"Action '{action}' on {email.gmail_id} deferred (confidence {confidence:.2f} < threshold {CONFIDENCE_THRESHOLDS.get(action, 0.65)})")
            self._log_action_attempted(email, action, "deferred", safety_decision)
            return False

        # In dry-run, just log the decision
        if self.safety_policy.dry_run:
            LOG.info(f"DRY RUN: would execute '{action}' on {email.gmail_id} (confidence {confidence:.2f})")
            self._log_action_attempted(email, action, "dry_run", safety_decision)
            return True

        # Execute the actual Gmail action
        try:
            if action == "label":
                self._apply_label(email.gmail_id, primary_label)
            elif action == "star":
                self._star_message(email.gmail_id)
            elif action == "mark_important":
                self._mark_important(email.gmail_id)
            elif action == "archive":
                self._archive_message(email.gmail_id)
            else:
                LOG.warning(f"Unknown action: {action}")
                return False

            LOG.info(f"Action '{action}' executed on {email.gmail_id}")
            self._log_action_executed(email, action, success=True)
            time.sleep(self.rate_limit_seconds)
            return True

        except HttpError as e:
            LOG.error(f"Gmail API error executing '{action}' on {email.gmail_id}: {e}")
            self._log_action_executed(email, action, success=False, error=str(e))
            return False
        except Exception as e:
            LOG.error(f"Unexpected error executing '{action}' on {email.gmail_id}: {e}", exc_info=True)
            self._log_action_executed(email, action, success=False, error=str(e))
            return False

    def _apply_label(self, message_id: str, label_name: str) -> None:
        """Apply a label to a message (create label if needed)."""
        # Get or create label ID
        label_id = self._get_or_create_label(label_name)
        if not label_id:
            raise ValueError(f"Could not create or find label: {label_name}")

        self.service.users().messages().modify(
            userId=self.user_id,
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    def _star_message(self, message_id: str) -> None:
        """Star a message (add STARRED label)."""
        self.service.users().messages().modify(
            userId=self.user_id,
            id=message_id,
            body={"addLabelIds": ["STARRED"]},
        ).execute()

    def _mark_important(self, message_id: str) -> None:
        """Mark message as important (add IMPORTANT label)."""
        self.service.users().messages().modify(
            userId=self.user_id,
            id=message_id,
            body={"addLabelIds": ["IMPORTANT"]},
        ).execute()

    def _archive_message(self, message_id: str) -> None:
        """Archive a message (remove INBOX label)."""
        self.service.users().messages().modify(
            userId=self.user_id,
            id=message_id,
            body={"removeLabelIds": ["INBOX"]},
        ).execute()

    def _get_or_create_label(self, label_name: str) -> Optional[str]:
        """Get label ID by name, or create if doesn't exist. Returns label ID."""
        try:
            # List all labels
            results = self.service.users().labels().list(userId=self.user_id).execute()
            labels = results.get("labels", [])
            for label in labels:
                if label["name"] == label_name:
                    return label["id"]

            # Create label if not found
            new_label = self.service.users().labels().create(
                userId=self.user_id,
                body={"name": label_name},
            ).execute()
            return new_label.get("id")
        except HttpError as e:
            LOG.error(f"Error managing label '{label_name}': {e}")
            return None

    def _log_action_attempted(
        self,
        email: Email,
        action: str,
        status: str,
        safety_decision: SafetyDecision,
    ) -> None:
        """Log an action attempt/decision to database (for audit trail).

        This records actions that were:
        - Blocked by safety policy
        - Skipped due to low confidence
        - Dry-run (not yet executed)
        """
        try:
            if self.db:
                action_record = ActionApplied(
                    email_gmail_id=email.gmail_id,
                    action=action,
                    params={"status": status, "reason": safety_decision.reason},
                    dry_run=self.safety_policy.dry_run or (status != "blocked" and status != "deferred"),
                    succeeded=None,  # None = not yet executed (only attempted)
                    details=f"{status}: {safety_decision.reason}",
                )
                self.db.log_action(action_record)
                LOG.debug(f"Logged action attempt: {action} on {email.gmail_id} ({status})")
        except Exception as e:
            LOG.warning(f"Could not log action attempt: {e}", exc_info=True)

    def _log_action_executed(
        self,
        email: Email,
        action: str,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Log an executed action to database (for audit trail).

        This records actions that were actually executed (or would be in dry-run mode).
        """
        try:
            if self.db:
                action_record = ActionApplied(
                    email_gmail_id=email.gmail_id,
                    action=action,
                    params={},
                    dry_run=self.safety_policy.dry_run,
                    succeeded=success,
                    details=error or "Success",
                )
                self.db.log_action(action_record)
                LOG.debug(f"Logged action execution: {action} on {email.gmail_id} ({'success' if success else 'failed'})")
        except Exception as e:
            LOG.warning(f"Could not log executed action: {e}", exc_info=True)

