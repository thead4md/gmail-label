"""Gmail fetcher wrapper for MailMind.

Provides small, testable wrappers around Gmail API calls: listing message ids,
fetching messages in batches, and applying labels.
"""
from __future__ import annotations

import logging
import socket
import ssl
import time
from typing import Optional, List, Dict, Any

from googleapiclient.errors import HttpError

try:  # google-auth ships a transport-level transient error; optional at import
    from google.auth.exceptions import TransportError as _GoogleTransportError
    _OPTIONAL_TRANSIENT: tuple = (_GoogleTransportError,)
except Exception:  # pragma: no cover - defensive
    _OPTIONAL_TRANSIENT = ()

# Transient failures worth retrying: Gmail HTTP errors plus low-level transport
# hiccups (reset connections, SSL blips, timeouts) that are NOT HttpError and
# would otherwise abort a whole fetch cycle on the first attempt.
_TRANSIENT_ERRORS: tuple = (
    HttpError, ConnectionError, TimeoutError, socket.timeout, ssl.SSLError,
) + _OPTIONAL_TRANSIENT

LOG = logging.getLogger(__name__)


def _retry(func, retries=3, backoff=1.0, allowed=_TRANSIENT_ERRORS):
    """Retry helper for HTTP/transport-bound operations.

    Retries on transient failures (Gmail HttpError plus connection/SSL/timeout
    errors), so a dropped connection mid-cycle is retried rather than bubbling up
    and aborting the fetch. Re-raises the last error once retries are exhausted.
    """
    for attempt in range(1, retries + 1):
        try:
            return func()
        except allowed as e:
            LOG.debug("Transient error on attempt %s: %s", attempt, e)
            if attempt == retries:
                raise
            time.sleep(backoff * attempt)
    # Only reachable if retries < 1 — never silently return None.
    raise RuntimeError("_retry called with retries < 1")


class GmailFetcher:
    def __init__(self, service, user_id: str = "me", rate_limit_seconds: float = 0.5):
        self.service = service
        self.user_id = user_id
        self.rate_limit_seconds = rate_limit_seconds

    def list_message_ids(
        self,
        label_ids: Optional[List[str]] = None,
        max_results: int = 100,
        query: Optional[str] = None,
    ) -> List[str]:
        """Return a list of message ids from the user's mailbox.

        This method pages through results until `max_results` or exhausted.

        Args:
            query: Optional Gmail search query (the API ``q`` parameter), e.g.
                ``"newer_than:3m"`` or ``"after:2026/03/02"``. Combined with
                ``label_ids`` (AND semantics). Used by the backfill command to
                pull a historical date range rather than just unread mail.
        """
        ids: List[str] = []
        page_token: Optional[str] = None

        while True:
            def call():
                return (
                    self.service.users()
                    .messages()
                    .list(userId=self.user_id, labelIds=label_ids or [], q=query,
                          pageToken=page_token, maxResults=min(500, max_results))
                    .execute()
                )

            resp = _retry(call)
            msgs = resp.get("messages", [])
            ids.extend([m["id"] for m in msgs])
            if len(ids) >= max_results:
                return ids[:max_results]
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
            time.sleep(self.rate_limit_seconds)

        return ids

    def get_message(self, message_id: str, format: str = "full") -> Dict[str, Any]:
        """Fetch a single message resource by id.

        `format` can be one of: minimal, full, raw, metadata
        """
        def call():
            return self.service.users().messages().get(userId=self.user_id, id=message_id, format=format).execute()

        resp = _retry(call)
        time.sleep(self.rate_limit_seconds)
        return resp

    def get_attachment(self, message_id: str, attachment_id: str) -> Dict[str, Any]:
        """Fetch a single attachment's bytes (base64url-encoded) by id.

        Called on-demand from the dashboard when a user opens an attachment —
        never during ingestion (attachments are stored as metadata only).
        """
        def call():
            return (
                self.service.users()
                .messages()
                .attachments()
                .get(userId=self.user_id, messageId=message_id, id=attachment_id)
                .execute()
            )

        resp = _retry(call)
        time.sleep(self.rate_limit_seconds)
        return resp

    def batch_get_messages(self, message_ids, format: str = "full"):
        """Fetch many messages in one HTTP batch (up to 100 per call).
        Returns a dict {message_id: raw_message_dict}. Messages that error are
        omitted. One rate-limit sleep per batch instead of one per message.
        """
        results: Dict[str, Any] = {}
        if not message_ids:
            return results
        def _make_callback():
            def _cb(request_id, response, exception):
                if exception is None and response is not None:
                    results[request_id] = response
            return _cb
        for start in range(0, len(message_ids), 100):
            chunk = message_ids[start:start + 100]
            def call(chunk=chunk):
                batch = self.service.new_batch_http_request(callback=_make_callback())
                for mid in chunk:
                    batch.add(
                        self.service.users().messages().get(
                            userId=self.user_id, id=mid, format=format),
                        request_id=mid,
                    )
                batch.execute()
                return None
            _retry(call)
            time.sleep(self.rate_limit_seconds)
        return results

    def ensure_label(
        self, label_name: str, color: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """Return the Gmail label id for ``label_name``, creating it if absent.

        Nested labels (e.g. 'MailMind/Work') are created automatically by Gmail
        when the name contains '/'. Returns None on API error.

        ``color`` is an optional Gmail color dict ({"backgroundColor": "#..",
        "textColor": "#.."}) using values from Gmail's allowed palette. It is set
        on create and patched onto an existing label. Colour failures are
        best-effort: they never block label creation or message labeling.
        """
        def _list():
            return self.service.users().labels().list(userId=self.user_id).execute()

        try:
            existing = _retry(_list).get("labels", [])
            for lab in existing:
                if lab.get("name") == label_name:
                    if color and lab.get("color") != color:
                        self.set_label_color(lab["id"], color)
                    return lab.get("id")

            body: Dict = {"name": label_name}
            if color:
                body["color"] = color

            def _create(body=body):
                return self.service.users().labels().create(
                    userId=self.user_id, body=body).execute()

            try:
                return _retry(_create).get("id")
            except HttpError as e:
                # Most likely an invalid colour pair — retry without colour so the
                # label is still created (just uncoloured).
                if color:
                    LOG.warning("ensure_label('%s'): colour rejected (%s); "
                                "creating without colour.", label_name, e)
                    def _create_plain():
                        return self.service.users().labels().create(
                            userId=self.user_id, body={"name": label_name}).execute()
                    return _retry(_create_plain).get("id")
                raise
        except HttpError as e:
            LOG.error("ensure_label('%s') failed: %s", label_name, e)
            return None

    def set_label_color(self, label_id: str, color: Dict[str, str]) -> bool:
        """Patch a label's colour. Best-effort — returns False on API error."""
        try:
            def _patch():
                return self.service.users().labels().patch(
                    userId=self.user_id, id=label_id, body={"color": color}).execute()
            _retry(_patch)
            return True
        except HttpError as e:
            LOG.warning("set_label_color(%s) failed: %s", label_id, e)
            return False

    def list_label_map(self) -> Dict[str, str]:
        """Return {label_id: name} for all labels in the mailbox."""
        def call():
            return self.service.users().labels().list(userId=self.user_id).execute()
        try:
            labs = _retry(call).get("labels", [])
            return {l["id"]: l["name"] for l in labs if l.get("id") and l.get("name")}
        except HttpError as e:
            LOG.error("list_label_map failed: %s", e)
            return {}

    def batch_add_label(self, message_ids: List[str], label_id: str) -> int:
        """Add ``label_id`` to many messages via Gmail's batchModify endpoint.

        Uses users.messages.batchModify (up to 1000 ids per atomic call) rather
        than firing one modify per message. The per-message approach silently
        dropped sub-requests under Gmail's modify rate limit; batchModify applies
        the label to the whole chunk in a single quota-cheap request and _retry
        backs off the whole call on transient errors. Idempotent (re-adding an
        existing label is a no-op). Returns the number of ids submitted.
        """
        if not message_ids or not label_id:
            return 0
        submitted = 0
        for start in range(0, len(message_ids), 1000):
            chunk = message_ids[start:start + 1000]

            def call(chunk=chunk):
                self.service.users().messages().batchModify(
                    userId=self.user_id,
                    body={"ids": chunk, "addLabelIds": [label_id]},
                ).execute()
                return None

            _retry(call)
            submitted += len(chunk)
            time.sleep(self.rate_limit_seconds)
        return submitted

