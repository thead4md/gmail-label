"""Feature extraction for MailMind ML classification.

Extracts feature vectors from Email and Prediction data models.
All features are derived from locally available data only (no remote calls).

Features used:
- Subject text (TF-IDF)
- Sender domain (categorical)
- Sender local part (patterns)
- Body snippet/preview text (TF-IDF)
- Recency (hours since received)
- Number of recipients
- Has unsubscribe signal (boolean)
- Has calendar signal (boolean)
- Has finance signal (boolean)
- Directly addressed signal (boolean)

This module is designed to be extensible for Phase 5+ without breaking changes.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from ..storage.models import Email
from ..taxonomy import ML_LABELS as VALID_LABELS
from ..intelligence.patterns import (
    UNSUBSCRIBE_RE,
    CALENDAR_RE,
    FINANCE_RE,
)

LOG = logging.getLogger(__name__)

# Local aliases for backward compatibility
_UNSUBSCRIBE_FEATURE_RE = UNSUBSCRIBE_RE
_CALENDAR_RE = CALENDAR_RE
_FINANCE_RE = FINANCE_RE


@dataclass
class FeatureVector:
    """Container for extracted features.

    Structured fields are provided for transparency and debugging.
    The actual vector used by the ML model is the flattened numerical representation.
    """
    # Text features (for TF-IDF or similar)
    subject: str = ""
    snippet: str = ""
    sender: str = ""

    # Categorical / structural features
    sender_domain: str = ""
    sender_local: str = ""
    num_recipients: int = 0
    recency_hours: Optional[float] = None

    # Boolean signals (mirroring rule signals for feature engineering)
    has_unsubscribe_signal: bool = False
    has_calendar_signal: bool = False
    has_finance_signal: bool = False
    is_directly_addressed: bool = False
    is_mass_cc: bool = False

    # Metadata
    email_gmail_id: str = ""
    true_label: Optional[str] = None  # For supervised training

    def to_text_corpus(self) -> str:
        """Combine text fields into a single corpus document for TF-IDF.

        This is the primary input to the baseline TF-IDF vectorizer.
        """
        parts = [self.subject, self.snippet, self.sender]
        return " ".join(p for p in parts if p)


def _detect_unsubscribe(text: str) -> bool:
    """Detect unsubscribe/list-management signals in text."""
    if not text:
        return False
    return bool(_UNSUBSCRIBE_FEATURE_RE.search(text.lower()))


def _detect_calendar(text: str) -> bool:
    """Detect calendar/invite signals in text."""
    if not text:
        return False
    return bool(_CALENDAR_RE.search(text.lower()))


def _detect_finance(text: str) -> bool:
    """Detect finance/payment signals in text."""
    if not text:
        return False
    return bool(_FINANCE_RE.search(text.lower()))


def build_model_text(subject, sender, snippet="", body_text="") -> str:
    """Canonical TF-IDF input — the SINGLE source of model text for BOTH training
    and inference.

    They previously diverged (training included body[:500]; inference's
    to_text_corpus did not), so the model trained on body features it never saw at
    inference. This unifies them and additionally appends engineered feature
    tokens, turning structured signals (unsubscribe / calendar / finance / sender
    domain / reply) into first-class TF-IDF features the classifier can weight.
    """
    subject = (subject or "").strip()
    snippet = (snippet or "").strip()
    sender = (sender or "").strip()
    body = (body_text or "")[:500]
    base = f"{subject} {snippet} {sender} {body}".strip()

    blob = f"{subject} {snippet} {body}".lower()
    tokens: List[str] = []
    if _UNSUBSCRIBE_FEATURE_RE.search(blob):
        tokens.append("feat_unsub")
    if _CALENDAR_RE.search(blob):
        tokens.append("feat_calendar")
    if _FINANCE_RE.search(blob):
        tokens.append("feat_finance")
    low_sender = sender.lower()
    if "@" in low_sender:
        domain = low_sender.split("@", 1)[1].strip(" <>")
        domain = re.sub(r"[^a-z0-9]+", "_", domain).strip("_")
        if domain:
            tokens.append(f"feat_domain_{domain}")
    if subject.lower().startswith(("re:", "re ", "fwd:", "fw:", "aw:")):
        tokens.append("feat_reply")

    return (f"{base} " + " ".join(tokens)).strip() if tokens else base


def build_content_text(subject, snippet="", body_text="") -> str:
    """Content-only TF-IDF input — no sender identity, no domain token.

    Used by the content channel of the 80/20 blend so sender information is
    not double-counted (it lives in the sender channel at 20% weight instead).
    Train and infer must both call this function — they share the invariant
    expressed in build_model_text's docstring.
    """
    subject = (subject or "").strip()
    snippet = (snippet or "").strip()
    body = (body_text or "")[:500]
    base = f"{subject} {snippet} {body}".strip()

    blob = base.lower()
    tokens: List[str] = []
    if _UNSUBSCRIBE_FEATURE_RE.search(blob):
        tokens.append("feat_unsub")
    if _CALENDAR_RE.search(blob):
        tokens.append("feat_calendar")
    if _FINANCE_RE.search(blob):
        tokens.append("feat_finance")
    if subject.lower().startswith(("re:", "re ", "fwd:", "fw:", "aw:")):
        tokens.append("feat_reply")

    return (f"{base} " + " ".join(tokens)).strip() if tokens else base


def extract_features(email: Email, true_label: Optional[str] = None) -> FeatureVector:
    """Extract a FeatureVector from an Email model.

    Args:
        email: Parsed Email model with extended attributes.
        true_label: Optional ground-truth label for supervised training.

    Returns:
        FeatureVector with all extracted features.
    """
    subject = email.subject or ""
    snippet = email.snippet or ""
    body = email.body_text or ""
    sender = email.sender or ""

    # Parse sender
    sender_domain = ""
    sender_local = ""
    if sender and "@" in sender:
        sender_local = sender.split("@")[0].lower()
        sender_domain = sender.split("@")[-1].lower()

    # Recipients
    recipients = email.recipients or []
    cc_addrs = getattr(email, "cc_addresses", []) or []
    all_recipients = len(recipients) + len(cc_addrs)

    # Recency
    recency_hours = None
    if email.date_ts:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        age_seconds = now_ts - email.date_ts
        recency_hours = age_seconds / 3600.0

    # Build combined text for signal detection
    combined_text = f"{subject} {snippet} {body}"

    return FeatureVector(
        subject=subject,
        snippet=snippet,
        sender=sender,
        sender_domain=sender_domain,
        sender_local=sender_local,
        num_recipients=all_recipients,
        recency_hours=recency_hours,
        has_unsubscribe_signal=_detect_unsubscribe(combined_text),
        has_calendar_signal=_detect_calendar(combined_text),
        has_finance_signal=_detect_finance(combined_text),
        is_directly_addressed=all_recipients <= 3 and not _detect_unsubscribe(combined_text),
        is_mass_cc=all_recipients > 5,
        email_gmail_id=email.gmail_id or "",
        true_label=true_label,
    )


def feature_vector_to_dict(fv: FeatureVector) -> Dict[str, Any]:
    """Convert a FeatureVector to a plain dict for logging/debugging."""
    return {
        "subject": fv.subject[:80] if fv.subject else "",
        "snippet": fv.snippet[:80] if fv.snippet else "",
        "sender": fv.sender,
        "sender_domain": fv.sender_domain,
        "num_recipients": fv.num_recipients,
        "recency_hours": round(fv.recency_hours, 2) if fv.recency_hours is not None else None,
        "has_unsubscribe_signal": fv.has_unsubscribe_signal,
        "has_calendar_signal": fv.has_calendar_signal,
        "has_finance_signal": fv.has_finance_signal,
        "is_directly_addressed": fv.is_directly_addressed,
        "is_mass_cc": fv.is_mass_cc,
    }
