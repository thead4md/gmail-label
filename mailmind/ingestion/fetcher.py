"""Gmail fetcher wrapper for MailMind.

Provides small, testable wrappers around Gmail API calls: listing message ids,
fetching messages, and polling for changes using historyId.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, List, Dict, Any, Callable

from googleapiclient.errors import HttpError

LOG = logging.getLogger(__name__)


def _retry(func, retries=3, backoff=1.0, allowed=(HttpError,)):
    """Simple retry helper for HTTP-bound operations."""
    for attempt in range(1, retries + 1):
        try:
            return func()
        except allowed as e:
            LOG.debug("HTTP error on attempt %s: %s", attempt, e)
            if attempt == retries:
                raise
            time.sleep(backoff * attempt)


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

    def ensure_label(self, label_name: str) -> Optional[str]:
        """Return the Gmail label id for ``label_name``, creating it if absent.

        Nested labels (e.g. 'MailMind/Work') are created automatically by Gmail
        when the name contains '/'. Returns None on API error.
        """
        def _list():
            return self.service.users().labels().list(userId=self.user_id).execute()

        try:
            existing = _retry(_list).get("labels", [])
            for lab in existing:
                if lab.get("name") == label_name:
                    return lab.get("id")

            def _create():
                return self.service.users().labels().create(
                    userId=self.user_id, body={"name": label_name}).execute()

            return _retry(_create).get("id")
        except HttpError as e:
            LOG.error("ensure_label('%s') failed: %s", label_name, e)
            return None

    def batch_add_label(self, message_ids: List[str], label_id: str) -> int:
        """Add ``label_id`` to many messages in batched HTTP requests (≤100/call).

        Adding a label a message already has is a no-op in Gmail, so this is
        idempotent. Returns the number of messages submitted.
        """
        if not message_ids or not label_id:
            return 0
        submitted = 0
        for start in range(0, len(message_ids), 100):
            chunk = message_ids[start:start + 100]

            def call(chunk=chunk):
                batch = self.service.new_batch_http_request()
                for mid in chunk:
                    batch.add(self.service.users().messages().modify(
                        userId=self.user_id, id=mid,
                        body={"addLabelIds": [label_id]}))
                batch.execute()
                return None

            _retry(call)
            submitted += len(chunk)
            time.sleep(self.rate_limit_seconds)
        return submitted

    def get_history(self, start_history_id: int, history_types: Optional[List[str]] = None) -> Dict[str, Any]:
        """Retrieve history records since `start_history_id`.

        Returns the raw history response which may include added messages and labels.
        """
        def call():
            return (
                self.service.users()
                .history()
                .list(userId=self.user_id, startHistoryId=str(start_history_id), historyTypes=history_types or ["messageAdded", "labelsAdded"]).execute()
            )

        return _retry(call)

    def poll_new_messages(self, start_history_id: int, handler: Callable[[Dict[str, Any]], None], poll_interval: int = 120):
        """Poll the Gmail history API and call `handler` for each new message item.

        This is a simple blocking poller intended for CLI/daemon use in MVP.
        It yields nothing and runs until interrupted.
        """
        current_history_id = start_history_id
        LOG.info("Starting history poller from historyId=%s", start_history_id)
        try:
            while True:
                try:
                    resp = self.get_history(current_history_id)
                except HttpError as e:
                    LOG.warning("History fetch failed: %s", e)
                    time.sleep(poll_interval)
                    continue

                if not resp:
                    time.sleep(poll_interval)
                    continue

                history = resp.get("history", [])
                for item in history:
                    # items can contain messageAdded entries
                    handler(item)
                # Update history id to the latest if provided
                if "historyId" in resp:
                    try:
                        current_history_id = int(resp["historyId"]) + 1
                    except Exception:
                        pass

                time.sleep(poll_interval)
        except KeyboardInterrupt:
            LOG.info("Poller interrupted by user")

