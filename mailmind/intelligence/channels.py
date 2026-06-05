"""MailMind — email channel detection.

Classifies each email into one of six communication channels using fast,
deterministic heuristics.  No LLM is called; this runs inline in the pipeline
before the ML and LLM tiers.

Channels
--------
newsletter    — subscribed bulk content (blogs, digests, product updates)
transactional — receipts, shipping, password resets, account notifications
team          — coworker or colleague emails (matching org domain)
personal      — one-to-one emails from real humans not in the org
marketing     — promotional / sales / cold outreach
automated     — monitoring alerts, CI/CD, bot messages
unknown       — none of the heuristics fired

Bilingual support: English + Hungarian.
Hungarian notes:
  - re.I handles Unicode case-fold for str objects in Python 3 (á→á, É→é, etc.)
  - Both accented and ASCII-transliterated forms are included where emails
    commonly omit diacritics (e.g. "szamla" alongside "számla").
  - Hungarian unsubscribe word: leiratkozás / leiratkoz (stem covers all forms)
  - "hírlevél" = newsletter; "értesítő" = notification/digest
"""
from __future__ import annotations

import re
from typing import Optional

from .patterns import UNSUBSCRIBE_RE, CALENDAR_RE

# ---------------------------------------------------------------------------
# Compiled patterns  (English + Hungarian, re.I for Unicode case-fold)
# ---------------------------------------------------------------------------

# Local aliases for canonical patterns
_UNSUB_RE = UNSUBSCRIBE_RE

_TRANSACTIONAL_SUBJECT_RE = re.compile(
    r"(order|receipt|invoice|confirmation|shipment|delivery|tracking|"
    r"your payment|charge|refund|reset.*password|verify.*email|"
    r"security alert|account.*activity|sign[-\s]?in|two[-\s]?factor|2fa|"
    # Hungarian
    r"megrendel[eé]s"       # order / megrendelés
    r"|sz[aá]mla"           # invoice / számla (szamla without accent)
    r"|nyugta"              # receipt
    r"|visszaigazol[aá]s"   # confirmation / visszaigazolás
    r"|sz[aá]ll[ií]t[aá]s" # shipping / szállítás
    r"|csomag"              # package
    r"|fizet[eé]s"          # payment / fizetés
    r"|[aá]tut[aá]l[aá]s" # transfer / átutalás
    r"|tranzakci[oó]"       # transaction / tranzakció
    r"|visszat[eé]r[ií]t[eé]s" # refund / visszatérítés
    r"|j[oó]v[aá][ií]r[aá]s"  # credit / jóváírás
    r"|jelszó.{0,20}(visszaállítás|módosítás|csere)"  # password reset
    r"|jelszo.{0,20}(visszaallitas|modositas)"         # without diacritics
    r"|e-?mail megerős[ií]t[eé]s"   # email verification
    r"|biztonsági riasztás"          # security alert
    r"|bejelentkezés"                # sign-in
    r"|k[eé]tl[eé]p[eé]ses|k[eé]tfaktoros"  # two-factor
    r"|fi[oó]k tev[eé]kenys[eé]g"   # account activity
    r"|csomagkövet[eé]s)",           # package tracking
    re.I | re.UNICODE,
)

_TRANSACTIONAL_SENDER_RE = re.compile(
    r"(no[-\s]?reply|noreply|donotreply|do[-\s]?not[-\s]?reply|"
    r"notification|alert|notify|support|billing|invoice|receipt|"
    r"automated|system|robot|bot@|mailer|"
    # Hungarian sender name fragments
    r"ertesites|értesítés"    # notification
    r"|riasztas|riasztás"     # alert
    r"|rendszer"              # system
    r"|automatikus"           # automated
    r"|szamlazas|számlázás"   # billing
    r"|ugyfelszolgalat|ügyfélszolgálat)",  # customer service (often transactional)
    re.I | re.UNICODE,
)

_MARKETING_SUBJECT_RE = re.compile(
    r"(% off|save \d|limited time|exclusive offer|deal|promo|discount|"
    r"flash sale|special offer|free.*trial|upgrade|last chance|"
    r"don.t miss|act now|today only|"
    # Hungarian
    r"kedvezm[eé]ny"      # discount / kedvezmény
    r"|akci[oó]"          # sale / akció
    r"|le[aá]raz[aá]s"   # markdown / leárazás
    r"|k[uü]l[oö]nleges ajánlat|k[uü]l[oö]nleges aj[aá]nlat"  # special offer
    r"|exkluz[ií]v aj[aá]nlat"   # exclusive offer
    r"|korl[aá]tozott ideig"     # limited time
    r"|ne hagyja ki|ne hagyd ki" # don't miss
    r"|ingyenes pr[oó]ba"        # free trial
    r"|csak ma"                  # today only
    r"|el ne szalaszd"           # don't miss it
    r"|aj[aá]nlatunk)",          # our offer
    re.I | re.UNICODE,
)

_NEWSLETTER_SENDER_RE = re.compile(
    r"(newsletter|digest|weekly|daily|roundup|substack|mailchimp|"
    r"sendgrid|constantcontact|hubspot|marketo|klaviyo|campaign|"
    # Hungarian platform / content markers
    r"hirlevel|h[ií]rlev[eé]l"   # newsletter
    r"|ertesito|értesítő"         # digest / notification
    r"|hetilap|napilevél"         # weekly/daily paper
    r"|k[oö]rlev[eé]l)",          # circular letter
    re.I | re.UNICODE,
)

_AUTOMATED_SUBJECT_RE = re.compile(
    r"(\[alert\]|\[notification\]|\[error\]|\[warning\]|"
    r"build (failed|passed|succeeded)|deploy|pipeline|ci |"
    r"server|uptime|monitor|nagios|pagerduty|sentry|datadog|"
    r"github.*action|workflow|"
    # Hungarian technical terms (often in mixed-language devops emails)
    r"rendszerhib[aá]"      # system error
    r"|\[figyelmeztet[eé]s\]"  # [warning]
    r"|\[hib[aá]\]"         # [error]
    r"|telep[ií]t[eé]s (sikeres|sikertelen)"  # deployment succeeded/failed
    r"|fut[aá]s sikertelen|fut[aá]s sikeres)"  # run failed/succeeded
    ,
    re.I | re.UNICODE,
)

_AUTOMATED_SENDER_RE = re.compile(
    r"(noreply@github|noreply@gitlab|notifications@|alerts@|"
    r"sentry@|datadog@|pagerduty|nagios|jenkins|circleci|travis)",
    re.I | re.UNICODE,
)

_GOOGLE_DOCS_RE = re.compile(
    r"(docs\.google\.com|drive-shares-noreply@google\.com|"
    r"comments-noreply@docs\.google\.com|"
    r"(commented|mentioned you|shared)( on| a)? (a )?(document|file|spreadsheet|presentation)|"
    r"megosztott (egy )?(dokumentumot|f[aá]jlt))",
    re.I | re.UNICODE,
)

_TASKS_RE = re.compile(
    r"(tasks-noreply@google\.com|marked .* complete|completed the task|"
    r"feladat (k[eé]sz|teljes[ií]tve|befejez))",
    re.I | re.UNICODE,
)

# Local alias for canonical calendar pattern
_CALENDAR_RE = CALENDAR_RE


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
    user_domain: The user's own email domain (e.g. 'company.hu') used to
                 identify team emails.  Pass None to skip team detection.
    """
    subj   = (subject  or "").strip()
    src    = (sender   or "").lower()
    body   = (body_text or "")[:500]
    corpus = f"{subj} {body}".lower()

    # ── 1. Automated / monitoring ───────────────────────────────────
    if _AUTOMATED_SUBJECT_RE.search(subj) or _AUTOMATED_SENDER_RE.search(src):
        return "automated"

    # ── Google Docs / Calendar / Tasks (specific, before generic buckets) ──
    if _CALENDAR_RE.search(subj) or _CALENDAR_RE.search(src):
        return "calendar"
    if _GOOGLE_DOCS_RE.search(corpus) or _GOOGLE_DOCS_RE.search(src):
        return "docs"
    if _TASKS_RE.search(corpus) or _TASKS_RE.search(src):
        return "tasks"

    # ── 2. Transactional (order / account / auth) ───────────────────
    if _TRANSACTIONAL_SUBJECT_RE.search(subj) or _TRANSACTIONAL_SENDER_RE.search(src):
        return "transactional"

    # ── 3. Newsletter (explicit list / unsub signals) ───────────────
    has_unsub             = bool(_UNSUB_RE.search(corpus))
    has_newsletter_sender = bool(_NEWSLETTER_SENDER_RE.search(src))
    if has_unsub or has_newsletter_sender:
        return "newsletter"

    # ── 4. Marketing (promo language — cold outreach) ───────────────
    if _MARKETING_SUBJECT_RE.search(subj):
        return "marketing"

    # ── 5. Team (same org domain) ───────────────────────────────────
    if user_domain:
        domain_match = re.search(r"@([\w.-]+)", src)
        if domain_match:
            sender_domain = domain_match.group(1).lower()
            if sender_domain == user_domain.lower():
                return "team"

    # ── 6. Personal — a real human sender address not caught above ───
    # (Reaching here already means no transactional/automated/etc. bucket fired,
    # so the old `if not _TRANSACTIONAL_SENDER_RE.search(src)` was always true and
    # `unknown` was dead. Gate on having a usable address instead.)
    if "@" in src:
        return "personal"

    # No usable sender and nothing else matched → genuinely unknown.
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
    """Detect and set pred.channel; also return the channel string."""
    channel = detect_channel(
        subject=getattr(email, "subject", None) or (email.get("subject") if isinstance(email, dict) else None),
        sender=getattr(email, "sender", None) or (email.get("sender") if isinstance(email, dict) else None),
        body_text=getattr(email, "body_text", None) or (email.get("body_text") if isinstance(email, dict) else None),
        user_domain=user_domain,
    )
    try:
        pred.channel = channel  # type: ignore[union-attr]
    except (AttributeError, TypeError):
        if isinstance(pred, dict):
            pred["channel"] = channel
    return channel
