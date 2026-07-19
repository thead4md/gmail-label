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
from ..ingestion.fetcher import GmailFetcher
from ..ingestion.gmail_label_colors import gmail_color_for
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
    # Belt-and-suspenders guard: "send" is never dispatched through
    # execute_action()'s confidence gate (send_message is a separate, direct-call-only
    # method — see below), but confidence is always in [0, 1], so 1.01 is
    # mathematically unreachable. If a future change ever accidentally routed "send"
    # through execute_action(), it would still be rejected here as a second,
    # independent line of defense beyond "it's not in the dispatch ladder".
    "send": 1.01,
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
        # Real-time labeling now goes through the same GmailFetcher.ensure_label
        # path the bulk `apply-labels` CLI command uses — retries via GmailFetcher's
        # shared _retry helper and supports Gmail label colours — instead of the
        # weaker bespoke get-or-create this class used to maintain itself.
        self._label_fetcher = GmailFetcher(self.service, user_id=self.user_id)

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
        confidence: Optional[float] = None,
    ) -> bool:
        """Execute an action on an email if safety policy permits.

        Args:
            email: Email to action.
            action: Action name (e.g., "label", "star", "archive").
            score: ScoreResult (its total_score is the *priority* score, 0-100).
            confidence: Classification confidence (0-1) for the CONFIDENCE_THRESHOLDS
                gate and safety policy. When None, falls back to score.total_score/100
                for back-compat — but callers should pass the real confidence:
                total_score is a PRIORITY score, not classification confidence, so
                using it here silently defers low-priority actions (e.g. archiving a
                newsletter, whose priority is ~0, never met the 0.85 archive gate).

        Returns:
            True if action was executed (or dry-run logged), False if blocked.
        """
        if confidence is None:
            confidence = score.total_score / 100.0
        primary_label = score.primary_label or "NOTIFICATION"

        # Check confidence threshold FIRST so deferred actions never consume
        # rate-limit budget inside safety_policy.evaluate().
        threshold = CONFIDENCE_THRESHOLDS.get(action, 0.65)
        if confidence < threshold:
            LOG.info(
                f"Action '{action}' on {email.gmail_id} deferred "
                f"(confidence {confidence:.2f} < threshold {threshold})"
            )
            deferred_decision = SafetyDecision(
                action=action, allowed=False,
                reason=f"Confidence {confidence:.2f} below threshold {threshold}",
            )
            self._log_action_attempted(email, action, "deferred", deferred_decision)
            return False

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

    def send_message(self, draft_row: dict, raw_mime_b64url: str) -> Optional[str]:
        """Send a fully-composed MIME message via the Gmail API.

        This is the ONLY place in this class that ever calls
        ``messages().send()`` — it is a real, irreversible send of outgoing mail,
        distinct from every other method in this file (which only ever modify
        labels on existing messages). It is intentionally NOT reachable through
        ``execute_action()``'s dispatch ladder or through
        ``QueueManager.enqueue_from_prediction()``: the only sanctioned caller is
        ``mailmind.intelligence.feedback.handle_approve_and_send``, which enforces
        that a draft must already be in 'approved' status (set by a separate, prior
        user interaction) before this is ever invoked.

        Args:
            draft_row: dict-like draft row (as returned by
                ``mailmind.storage.queries.get_draft``) — used here only for its
                ``to_addrs`` (logging) and ``thread_id`` (so Gmail threads the sent
                message correctly when replying).
            raw_mime_b64url: base64url-encoded RFC 2822 MIME message, as produced by
                ``mailmind.compose.composer.build_reply_mime`` /
                ``build_new_message_mime``.

        Returns:
            - The literal string ``"dry_run"`` if ``safety_policy.dry_run`` is True —
              a truthy sentinel distinct from any real Gmail message id, so callers can
              tell a dry-run apart from a real send. The Gmail API is NEVER called in
              this case.
            - ``None`` if rate-limited (refused, nothing sent) or if the Gmail API call
              fails.
            - The real Gmail message id (str) on a successful send.
        """
        # Dry-run check FIRST and unconditionally: there is no way to reach the
        # Gmail API call below while dry_run is True. This is the single most
        # important line in this method — "dry_run is the default everywhere" gets
        # no exception for sending.
        if self.safety_policy.dry_run:
            LOG.info(f"DRY RUN: would send message to {draft_row.get('to_addrs')}")
            return "dry_run"

        if self.safety_policy._is_rate_limited():
            LOG.warning(
                f"Send to {draft_row.get('to_addrs')} refused: rate limit exceeded "
                f"({self.safety_policy.max_actions_per_hour} actions/hour)"
            )
            return None

        try:
            body = {"raw": raw_mime_b64url}
            thread_id = draft_row.get("thread_id")
            if thread_id:
                body["threadId"] = thread_id
            response = self.service.users().messages().send(
                userId=self.user_id,
                body=body,
            ).execute()
            message_id = response.get("id") if response else None
            LOG.info(f"Sent message to {draft_row.get('to_addrs')} (gmail id: {message_id})")
            self._log_send(draft_row, success=True, gmail_message_id=message_id)
            time.sleep(self.rate_limit_seconds)
            return message_id
        except HttpError as e:
            LOG.error(f"Gmail API error sending message to {draft_row.get('to_addrs')}: {e}")
            self._log_send(draft_row, success=False, error=str(e))
            return None
        except Exception as e:
            LOG.error(
                f"Unexpected error sending message to {draft_row.get('to_addrs')}: {e}",
                exc_info=True,
            )
            self._log_send(draft_row, success=False, error=str(e))
            return None

    def _log_send(
        self,
        draft_row: dict,
        success: bool,
        gmail_message_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Log a send attempt to the action_applied audit trail (best-effort)."""
        try:
            if self.db:
                action_record = ActionApplied(
                    email_gmail_id=draft_row.get("in_reply_to_gmail_id") or "",
                    action="send",
                    params={"draft_id": draft_row.get("id"), "to_addrs": draft_row.get("to_addrs")},
                    dry_run=False,
                    succeeded=success,
                    details=(f"gmail_message_id={gmail_message_id}" if success else (error or "Failed")),
                )
                self.db.log_action(action_record)
                LOG.debug(f"Logged send attempt for draft {draft_row.get('id')} ({'success' if success else 'failed'})")
        except Exception as e:
            LOG.warning(f"Could not log send attempt: {e}", exc_info=True)

    def _apply_label(self, message_id: str, label_name: str) -> None:
        """Apply a label to a message (create label if needed)."""
        # Get or create label ID (with retry + colour support via GmailFetcher).
        label_id = self._label_fetcher.ensure_label(
            label_name, color=gmail_color_for(label_name)
        )
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

