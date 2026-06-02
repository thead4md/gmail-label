"""PR B: batch_get_messages fetches in chunks and returns id->raw map."""
from __future__ import annotations
from unittest.mock import MagicMock
from mailmind.ingestion.fetcher import GmailFetcher


def test_batch_get_messages_collects_via_callback():
    service = MagicMock()
    captured = {}
    def new_batch(callback=None):
        b = MagicMock()
        b._cb = callback
        added = []
        b.add.side_effect = lambda req, request_id=None: added.append(request_id)
        def execute():
            for rid in added:
                b._cb(rid, {"id": rid, "payload": {}}, None)
        b.execute.side_effect = execute
        captured["batch"] = b
        return b
    service.new_batch_http_request.side_effect = new_batch
    f = GmailFetcher(service, rate_limit_seconds=0)
    out = f.batch_get_messages(["a", "b", "c"])
    assert set(out.keys()) == {"a", "b", "c"}
    assert out["a"]["id"] == "a"


def test_batch_get_messages_empty():
    f = GmailFetcher(MagicMock(), rate_limit_seconds=0)
    assert f.batch_get_messages([]) == {}
