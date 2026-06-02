"""MailMind — email channel detection.

Classifies each email into one of six communication channels using fast,
deterministic heuristics.  No LLM is called; this runs inline in the pipeline
before the ML and LLM tiers.

Channels
--------
newsletter    — subscribed bulk content (blogs, digests, product updates)
transactional — receipts, shipping, password resets, account notifications
team          — coworker or colleague emails (matching org domain / company domains)
personal      — one-to-one emails from real humans not in the org
marketing     — promotional / sales / cold outreach
automated     — monitoring alerts, CI/CD, bot messages
unknown       — none of the heuristics fired
"""
from __future__ import annotations

import re
from typing import Optional

_LIST_ID_RE   = re.compile(r"list-id\s*:", re.I)
_UNSUB_RE     = re.compile(
    r"(unsubscribe|opt[ -]?out|manage.*preference|email.*preference|"
    r"view.*in.*browser|click here to unsubscribe)",
    re.I,
)
_TRANSACTIONAL_SUBJECT_RE = re.compile(
    r"(order|receipt|invoice|confirmation|shipment|delivery|tracking|"
    r"your payment|charge|refund|reset.*password|verify.*email|"
    r"security alert|account.*activity|sign[-\s]?in|two[-\s]?factor|2fa)",
    re.I,
)
_TRANSACTIONAL_SENDER_RE = re.compile(
    r"(no[-\s]?reply|noreply|donotreply|do[-\s]?not[-\s]?reply|"
    r"notification|alert|notify|support|billing|invoice|receipt|"
    r"automated|system|robot|bot@|mailer)",
    re.I,
)
_MARKETING_SUBJECT_RE = re.compile(
    r"(% off|save \d|limited time|exclusive offer|deal|promo|discount|"
    r"flash sale|special offer|free.*trial|upgrade|last chance|"
    r"don.t miss|act now|today only)",
    re.I,
)
_NEWSLETTER_SENDER_RE = re.compile(
    r"(newsletter|digest|weekly|daily|roundup|substack|mailchimp|"
    r"sendgrid|constantcontact|hubspot|marketo|klaviyo|campaign)",
    re.I,
)
_AUTOMATED_SUBJECT_RE = re.compile(
    r"(\[alert\]|\[notification\]|\[error\]|\[warning\]|"
    r"build (failed|passed|succeeded)|deploy|pipeline|ci |"
    r"server|uptime|monitor|nagios|pagerduty|sentry|datadog|"
    r"github.*action|workflow)",
    re.I,
)
_AUTOMATED_SENDER_RE = re.compile(
    r"(noreply@github|noreply@gitlab|notifications@|alerts@|"
    r"sentry@|datadog@|pagerduty|nagios|jenkins|circleci|travis)",
    re.I,
)


def detect_channel(
    subject: Optional[str],
    sender: Optional[str],
    body_text: Optional[str],
    *,
    user_domain: Optional[str] = None,
) -> str:
    """Return a channel label for the email.

    Parameters
    ----------
    subject:     Email subject line.
    sender:      Sender email address (or display-name <addr>).
    body_text:   First 500 characters of body text.
    user_domain: The user's own email domain (e.g. 'company.com') used to
                 identify team emails.  Pass None to skip team detection.
    """
    subj   = (subject  or "").strip()
    src    = (sender   or "").lower()
    body   = (body_text or "")[:500].lower()
    corpus = f"{subj} {body}".lower()

    # ── 1. Automated / monitoring ───────────────────────────────────
    if _AUTOMATED_SUBJECT_RE.search(subj) or _AUTOMATED_SENDER_RE.search(src):
        return "automated"

    # ── 2. Transactional (order / account / auth) ───────────────────
    if _TRANSACTIONAL_SUBJECT_RE.search(subj) or _TRANSACTIONAL_SENDER_RE.search(src):
        return "transactional"

    # ── 3. Newsletter (explicit list / unsub signals) ───────────────
    has_unsub   = bool(_UNSUB_RE.search(corpus))
    has_newsletter_sender = bool(_NEWSLETTER_SENDER_RE.search(src))
    if has_unsub or has_newsletter_sender:
        return "newsletter"

    # ── 4. Marketing (promo language, no unsub in body — cold outreach) ──
    if _MARKETING_SUBJECT_RE.search(subj):
        return "marketing"

    # ── 5. Team (same org domain) ───────────────────────────────────
    if user_domain:
        # extract domain from sender address
        domain_match = re.search(r"@([\w.-]+)", src)
        if domain_match:
            sender_domain = domain_match.group(1).lower()
            if sender_domain == user_domain.lower():
                return "team"

    # ── 6. Personal — anything not caught above with a human address ─
    # Avoid classifying no-reply / notification senders as personal
    if not _TRANSACTIONAL_SENDER_RE.search(src):
        return "personal"

    return "unknown"


# ---------------------------------------------------------------------------
# Convenience: enrich a Prediction object (or dict) with channel field
# ---------------------------------------------------------------------------

def enrich_prediction_with_channel(
    pred,
    email,
    *,
    user_domain: Optional[str] = None,
) -> str:
    """Detect and set pred.channel; also return the channel string.

    Works with both dataclass Prediction and plain dict.
    """
    channel = detect_channel(
        subject=getattr(email, "subject", None) or (email.get("subject") if isinstance(email, dict) else None),
        sender=getattr(email, "sender", None) or (email.get("sender") if isinstance(email, dict) else None),
        body_text=getattr(email, "body_text", None) or (email.get("body_text") if isinstance(email, dict) else None),
        user_domain=user_domain,
    )
    # Attach to prediction if it supports attribute assignment
    try:
        pred.channel = channel  # type: ignore[union-attr]
    except (AttributeError, TypeError):
        if isinstance(pred, dict):
            pred["channel"] = channel
    return channel
