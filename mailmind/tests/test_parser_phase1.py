"""Tests for Phase 1 parser extraction: attachments, threading headers,
history_id, and body_html — promoted from dynamic setattr to real Email
dataclass fields.
"""
from __future__ import annotations

from mailmind.ingestion.parser import _collect_attachments, parse_message


def _msg(headers=None, payload_extra=None, history_id=None, msg_id="test_123"):
    resource = {
        "id": msg_id,
        "threadId": "thread_456",
        "snippet": "Test email",
        "labelIds": [],
        "payload": {
            "headers": headers or [
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Test"},
                {"name": "Date", "value": "Mon, 01 Jan 2024 12:00:00 +0000"},
            ],
            "body": {"data": ""},
        },
    }
    if payload_extra:
        resource["payload"].update(payload_extra)
    if history_id is not None:
        resource["historyId"] = history_id
    return resource


def test_parse_message_sets_threading_headers():
    resource = _msg(headers=[
        {"name": "From", "value": "sender@example.com"},
        {"name": "To", "value": "me@example.com"},
        {"name": "Subject", "value": "Re: Test"},
        {"name": "Date", "value": "Mon, 01 Jan 2024 12:00:00 +0000"},
        {"name": "Message-ID", "value": "<abc123@example.com>"},
        {"name": "In-Reply-To", "value": "<orig123@example.com>"},
        {"name": "References", "value": "<orig123@example.com> <mid1@example.com>"},
    ])
    email = parse_message(resource)
    assert email.message_id == "<abc123@example.com>"
    assert email.in_reply_to == "<orig123@example.com>"
    assert email.references_header == "<orig123@example.com> <mid1@example.com>"


def test_parse_message_threading_headers_absent_are_none():
    email = parse_message(_msg())
    assert email.message_id is None
    assert email.in_reply_to is None
    assert email.references_header is None


def test_parse_message_sets_history_id():
    email = parse_message(_msg(history_id="98765"))
    assert email.history_id == 98765


def test_parse_message_history_id_absent_is_none():
    email = parse_message(_msg())
    assert email.history_id is None


def test_parse_message_sets_body_html_alongside_body_text():
    import base64

    html_content = "<html><body><p>Hello <b>World</b></p></body></html>"
    encoded = base64.urlsafe_b64encode(html_content.encode("utf-8")).decode("ascii")
    resource = _msg(payload_extra={
        "mimeType": "text/html",
        "body": {"data": encoded},
    })
    email = parse_message(resource)
    assert email.body_html == html_content
    # body_text stays the flattened plain-text version.
    assert "Hello" in (email.body_text or "")
    assert "<b>" not in (email.body_text or "")


def test_parse_message_no_html_parts_body_html_is_none():
    import base64

    plain = "Hello plain text"
    encoded = base64.urlsafe_b64encode(plain.encode("utf-8")).decode("ascii")
    resource = _msg(payload_extra={
        "mimeType": "text/plain",
        "body": {"data": encoded},
    })
    email = parse_message(resource)
    assert email.body_html is None
    assert email.body_text == plain


def test_collect_attachments_finds_top_level_attachment():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": ""}},
            {
                "mimeType": "application/pdf",
                "filename": "invoice.pdf",
                "body": {"attachmentId": "att-1", "size": 1024},
            },
        ],
    }
    out = []
    _collect_attachments(payload, out)
    assert out == [{
        "filename": "invoice.pdf",
        "mimeType": "application/pdf",
        "size": 1024,
        "attachmentId": "att-1",
    }]


def test_collect_attachments_recurses_into_nested_parts():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": ""}},
                    {"mimeType": "text/html", "body": {"data": ""}},
                ],
            },
            {
                "mimeType": "image/png",
                "filename": "screenshot.png",
                "body": {"attachmentId": "att-2", "size": 2048},
            },
        ],
    }
    out = []
    _collect_attachments(payload, out)
    assert len(out) == 1
    assert out[0]["attachmentId"] == "att-2"
    assert out[0]["filename"] == "screenshot.png"


def test_collect_attachments_ignores_inline_body_parts():
    payload = {
        "mimeType": "text/plain",
        "body": {"data": "aGVsbG8="},
    }
    out = []
    _collect_attachments(payload, out)
    assert out == []


def test_parse_message_populates_attachments_dynamic_attribute():
    resource = _msg(payload_extra={
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": ""}},
            {
                "mimeType": "application/pdf",
                "filename": "invoice.pdf",
                "body": {"attachmentId": "att-1", "size": 1024},
            },
        ],
    })
    email = parse_message(resource)
    attachments = getattr(email, "attachments", None)
    assert attachments == [{
        "filename": "invoice.pdf",
        "mimeType": "application/pdf",
        "size": 1024,
        "attachmentId": "att-1",
    }]


def test_parse_message_no_attachments_is_empty_list():
    email = parse_message(_msg())
    assert getattr(email, "attachments", None) == []
