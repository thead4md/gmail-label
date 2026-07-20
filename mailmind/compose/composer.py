"""MIME composer for MailMind (Phase 3B).

Pure, standalone message-construction logic for Gmail API sends. This module makes
**no** Gmail API calls and has **no** database dependency — it only builds RFC 2822
MIME messages and returns them as base64url-encoded strings, ready to be passed as
``body={"raw": <result>}`` to ``users().messages().send()`` (or ``.create()`` for a
draft) by a later phase's executor code.

Design notes (see the Phase 3 plan, "Compose: plain-text first"):
- Bodies are always built as ``multipart/alternative`` with a verbatim ``text/plain``
  part plus a minimal, mechanically-derived ``text/html`` part (HTML-escaped, newlines
  turned into ``<br>``). This is intentionally *not* real Markdown/rich-text rendering.
- ``From`` is left unset unless the caller passes ``from_addr`` explicitly, so Gmail's
  API fills in the authenticated user's own address on send. Never hardcode a fake
  address here.
- Non-ASCII subjects (this mailbox has plenty of Hungarian names/subjects) are encoded
  correctly via ``email.mime.text.MIMEText``'s Subject header handling / ``email.header.Header``.
"""
from __future__ import annotations

import base64
import html
from datetime import datetime, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

__all__ = [
    "build_reply_mime",
    "build_new_message_mime",
    "quote_original_body",
    "reply_subject",
]


def reply_subject(subject: str) -> str:
    """Prefix *subject* with "Re: " unless it already starts with that
    (case-insensitive), matching normal mail-client behavior. Shared by
    build_reply_mime and the dashboard's reply-compose UI so a reply to an
    already-"Re:"-prefixed subject never doubles up into "Re: Re: ...".
    """
    subject = subject or ""
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"


def _encode_header(value: str) -> Header:
    """Return an ``email.header.Header`` that safely carries non-ASCII text.

    Uses utf-8 encoding only when the value isn't plain ASCII, matching what
    ``email.mime.text.MIMEText`` does internally for its own headers.
    """
    try:
        value.encode("ascii")
        return Header(value, "ascii")
    except UnicodeEncodeError:
        return Header(value, "utf-8")


def _normalize_message_id(message_id: str) -> str:
    """Ensure a Message-ID-shaped value is wrapped in angle brackets.

    Gmail/RFC 5322 expect ``In-Reply-To``/``References`` values wrapped like
    ``<abc123@mail.gmail.com>``. Accepts a value that already has brackets, and adds
    them if missing. Strips surrounding whitespace first.
    """
    mid = message_id.strip()
    if not mid:
        return mid
    if not mid.startswith("<"):
        mid = "<" + mid
    if not mid.endswith(">"):
        mid = mid + ">"
    return mid


def _text_to_html(body_text: str) -> str:
    """Naive, mechanical text->HTML conversion: escape then turn newlines into <br>.

    Intentionally minimal per the plan's "plain-text first" decision — no Markdown,
    no rich formatting, just enough so a reasonable HTML client renders line breaks.
    """
    escaped = html.escape(body_text)
    return escaped.replace("\n", "<br>\n")


def _build_mime_message(
    *,
    to_addr: str,
    subject: str,
    body_text: str,
    from_addr: Optional[str] = None,
    cc_addr: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> MIMEMultipart:
    """Shared builder used by both public entry points.

    Builds a multipart/alternative (text/plain + auto-generated text/html) message
    with the given headers. Does not touch Gmail/network/DB in any way.
    """
    msg = MIMEMultipart("alternative")

    msg["To"] = to_addr
    if cc_addr:
        msg["Cc"] = cc_addr
    if from_addr:
        msg["From"] = from_addr
    msg["Subject"] = _encode_header(subject)

    if in_reply_to:
        normalized = _normalize_message_id(in_reply_to)
        msg["In-Reply-To"] = normalized
        if references:
            # Chain onto the existing References header rather than overwriting it,
            # per RFC 5322 (space-separated list of Message-IDs, oldest first).
            existing = references.strip()
            if normalized not in existing.split():
                msg["References"] = f"{existing} {normalized}".strip()
            else:
                msg["References"] = existing
        else:
            msg["References"] = normalized

    plain_part = MIMEText(body_text, "plain", "utf-8")
    html_part = MIMEText(_text_to_html(body_text), "html", "utf-8")
    # RFC 2046: the LAST part of a multipart/alternative is the preferred / richest
    # rendering, so attach plain first, html second.
    msg.attach(plain_part)
    msg.attach(html_part)

    return msg


def _message_to_raw_b64url(msg: MIMEMultipart) -> str:
    """Serialize a MIME message to the base64url string Gmail's API expects."""
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def build_reply_mime(
    *,
    to_addr: str,
    subject: str,
    body_text: str,
    in_reply_to_message_id: Optional[str] = None,
    references: Optional[str] = None,
    cc_addr: Optional[str] = None,
    from_addr: Optional[str] = None,
) -> str:
    """Build a Gmail-API-ready reply MIME message, returned as a base64url string.

    - ``subject`` is prefixed with "Re: " if it doesn't already start with that
      (case-insensitive check), matching normal mail-client behavior.
    - ``in_reply_to_message_id``, if given, becomes the ``In-Reply-To`` header
      (angle-bracket-normalized). If ``references`` (the original message's own
      References/In-Reply-To chain) is also given, the new message's ``References``
      header is the existing chain with the new id appended — never a bare overwrite.
    - ``from_addr`` is optional and normally omitted so Gmail's API auto-fills the
      authenticated user's own address on send.
    """
    final_subject = reply_subject(subject)

    msg = _build_mime_message(
        to_addr=to_addr,
        subject=final_subject,
        body_text=body_text,
        from_addr=from_addr,
        cc_addr=cc_addr,
        in_reply_to=in_reply_to_message_id,
        references=references,
    )
    return _message_to_raw_b64url(msg)


def build_new_message_mime(
    *,
    to_addr: str,
    subject: str,
    body_text: str,
    cc_addr: Optional[str] = None,
    from_addr: Optional[str] = None,
) -> str:
    """Build a Gmail-API-ready fresh-compose MIME message, as a base64url string.

    Same multipart/alternative (text/plain + auto-html) construction as
    ``build_reply_mime``, but with no In-Reply-To/References threading headers.
    """
    msg = _build_mime_message(
        to_addr=to_addr,
        subject=subject,
        body_text=body_text,
        from_addr=from_addr,
        cc_addr=cc_addr,
    )
    return _message_to_raw_b64url(msg)


def quote_original_body(
    original_sender: str,
    original_date_ts: int,
    original_body_text: str,
) -> str:
    """Build a conventional "On [date], [sender] wrote:" quote block.

    Prefixes every line of ``original_body_text`` with "> ", for a reply UI to
    optionally prepend ahead of the user's typed reply. Pure string formatting —
    no Gmail/DB dependency.
    """
    dt = datetime.fromtimestamp(original_date_ts, tz=timezone.utc)
    date_str = dt.strftime("%a, %b %d, %Y at %I:%M %p UTC")
    header = f"On {date_str}, {original_sender} wrote:"
    quoted_lines = "\n".join(f"> {line}" for line in original_body_text.split("\n"))
    return f"{header}\n{quoted_lines}"
