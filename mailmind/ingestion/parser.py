"""Parser converting Gmail API message payloads into MailMind Email models.

This parser is deterministic and avoids network calls; it decodes message
payloads, extracts headers, prefers plain text, and falls back to stripped
HTML when needed.
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Dict, Any, List, Optional
from email.utils import parsedate_to_datetime, parseaddr
import html

from ..storage.models import Email

LOG = logging.getLogger(__name__)


def _b64url_decode(data: str) -> bytes:
    # Gmail API uses URL-safe base64 without padding
    if not data:
        return b""
    data = data.replace("-", "+").replace("_", "/")
    padding = len(data) % 4
    if padding:
        data += "=" * (4 - padding)
    return base64.b64decode(data)


def _collect_parts(payload: Dict[str, Any], plain_parts: List[str], html_parts: List[str], mime_types: List[str]) -> None:
    mime = payload.get("mimeType")
    if mime:
        mime_types.append(mime)

    body = payload.get("body", {})
    data = body.get("data")
    if data and mime in ("text/plain", "text/html"):
        decoded = _b64url_decode(data).decode("utf-8", errors="replace")
        if mime == "text/plain":
            plain_parts.append(decoded)
        else:
            html_parts.append(decoded)

    for part in payload.get("parts", []) or []:
        _collect_parts(part, plain_parts, html_parts, mime_types)


def _html_to_text(html_content: str) -> str:
    # Basic conversion: unescape HTML entities and strip tags
    text = html.unescape(html_content)
    # Remove script/style
    text = re.sub(r"(?s)<(script|style).*?>.*?</\1>", "", text)
    # Replace tags with whitespace
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_unsubscribe_url(list_unsubscribe: Optional[str]) -> Optional[str]:
    """Extract HTTPS or mailto URL from List-Unsubscribe header.

    Format: List-Unsubscribe: <https://x>, <mailto:u@x>
    Prefer HTTPS URLs over mailto. Return None if no valid URL found.
    """
    if not list_unsubscribe:
        return None

    urls = re.findall(r"<(https?://[^>]+)>", list_unsubscribe, re.I)
    if urls:
        return urls[0]

    # Fall back to mailto if no https URL
    mailto = re.findall(r"<(mailto:[^>]+)>", list_unsubscribe, re.I)
    if mailto:
        return mailto[0]

    return None


def _parse_headers(headers: List[Dict[str, str]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for h in headers:
        name = h.get("name", "").lower()
        value = h.get("value", "")
        out.setdefault(name, []).append(value)
    return out


def parse_message(resource: Dict[str, Any]) -> Email:
    """Convert a Gmail API message resource (format=full) into an Email model."""
    msg_id = resource.get("id")
    thread_id = resource.get("threadId")
    snippet = resource.get("snippet")
    labels = resource.get("labelIds") or []

    payload = resource.get("payload", {})
    headers = payload.get("headers", [])
    hdrs = _parse_headers(headers)

    # From
    from_raw = hdrs.get("from", [""])[0]
    sender_name, sender_email = parseaddr(from_raw)
    sender_domain = None
    if "@" in sender_email:
        sender_domain = sender_email.split("@", 1)[1].lower()

    # To / Cc
    to_addrs = []
    for v in hdrs.get("to", []):
        # split on comma
        for part in v.split(","):
            name, addr = parseaddr(part)
            if addr:
                to_addrs.append(addr)
    cc_addrs = []
    for v in hdrs.get("cc", []):
        for part in v.split(","):
            name, addr = parseaddr(part)
            if addr:
                cc_addrs.append(addr)

    # Subject
    subject = hdrs.get("subject", [None])[0]

    # Date
    received_at = None
    date_hdr = hdrs.get("date", [None])[0]
    if date_hdr:
        try:
            dt = parsedate_to_datetime(date_hdr)
            received_at = int(dt.timestamp())
        except Exception:
            LOG.debug("Failed to parse Date header: %s", date_hdr)

    # Collect body parts
    plain_parts: List[str] = []
    html_parts: List[str] = []
    mime_types: List[str] = []
    _collect_parts(payload, plain_parts, html_parts, mime_types)

    body_text: Optional[str] = None
    if plain_parts:
        body_text = "\n\n".join(plain_parts).strip()
    elif html_parts:
        body_text = _html_to_text(html_parts[0])

    # history id
    history_id = None
    h = resource.get("historyId")
    if h:
        try:
            history_id = int(h)
        except Exception:
            history_id = None

    # List-Unsubscribe header (prefer https URL, fall back to mailto)
    list_unsubscribe_hdr = hdrs.get("list-unsubscribe", [None])[0]
    unsubscribe_url = _extract_unsubscribe_url(list_unsubscribe_hdr)

    email = Email(
        gmail_id=msg_id,
        thread_id=thread_id,
        sender=sender_email or None,
        recipients=(to_addrs + cc_addrs),
        subject=subject,
        snippet=snippet,
        body_text=body_text,
        date_ts=received_at,
        labels=labels,
        parsed=True,
        unsubscribe_url=unsubscribe_url,
    )

    # Attach additional raw headers if needed in a safe manner
    # (do not store full raw body here unless privacy settings allow it)
    # We'll add a raw_headers attribute dynamically for consumers who opt-in.
    try:
        setattr(email, "raw_headers", hdrs)
        setattr(email, "sender_name", sender_name or None)
        setattr(email, "sender_domain", sender_domain)
        setattr(email, "to_addresses", to_addrs)
        setattr(email, "cc_addresses", cc_addrs)
        setattr(email, "mime_types", list(dict.fromkeys(mime_types)))
        setattr(email, "history_id", history_id)
    except Exception:
        LOG.debug("Failed to set extended attributes on Email dataclass", exc_info=True)

    return email


class GmailMessageParser:
    """Convenience wrapper for parsing Gmail API message payloads."""

    def parse(self, resource: Dict[str, Any]) -> Email:
        """Parse a Gmail API message resource into an Email model."""
        return parse_message(resource)

