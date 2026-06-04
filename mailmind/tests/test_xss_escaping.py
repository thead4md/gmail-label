"""Regression test for stored-XSS vulnerability fix.

Ensures that all untrusted email content (sender, subject, labels, action items,
deadlines, etc.) is properly HTML-escaped before being rendered with unsafe_allow_html=True.
"""

import pytest

from mailmind.dashboard.helpers import (
    action_items_html,
    corrections_table_html,
    deadline_pill_html,
    email_card_html,
    label_chip_html,
    sender_table_html,
)


def test_label_chip_html_escapes_xss():
    """Label chip should escape HTML characters in label names."""
    payload = "<img src=x onerror=alert(1)>"
    result = label_chip_html(payload)
    assert "<img" not in result
    # Labels are uppercased, so the escaped output is uppercase
    assert "&lt;IMG" in result


def test_email_card_html_escapes_sender():
    """Email card should escape HTML characters in sender name."""
    # Note: sender parsing splits on '<' to extract display name, so we test
    # with entities that survive the split and must be escaped
    payload = 'Test & <script>alert(1)</script>'
    result = email_card_html(
        subject="Test",
        sender=payload,
        time_ago="1h ago",
    )
    # The part after '<' is removed by split, but '&' should be escaped
    assert "&amp;" in result
    # And '<script>' portion is removed, so we don't see it
    assert "<script>" not in result


def test_email_card_html_escapes_subject():
    """Email card should escape HTML characters in subject."""
    payload = "<img src=x onerror=alert(1)>"
    result = email_card_html(
        subject=payload,
        sender="test@example.com",
        time_ago="1h ago",
    )
    assert "<img" not in result
    assert "&lt;img" in result


def test_email_card_html_escapes_thread_summary():
    """Email card should escape HTML characters in thread summary."""
    payload = "<img src=x onerror=alert(1)>"
    result = email_card_html(
        subject="Test",
        sender="test@example.com",
        time_ago="1h ago",
        thread_summary=payload,
    )
    assert "<img" not in result
    assert "&lt;img" in result


def test_action_items_html_escapes_items():
    """Action items should escape HTML characters in item text."""
    payload = "<img src=x onerror=alert(1)>"
    result = action_items_html([payload])
    assert "<img" not in result
    assert "&lt;img" in result


def test_deadline_pill_html_escapes_deadline():
    """Deadline pill should escape HTML characters in deadline text."""
    payload = "<img src=x onerror=alert(1)>"
    result = deadline_pill_html([payload])
    assert "<img" not in result
    assert "&lt;img" in result


def test_sender_table_html_escapes_email():
    """Sender table should escape HTML characters in email addresses."""
    payload = "<img src=x onerror=alert(1)>@example.com"
    result = sender_table_html([{
        "sender_email": payload,
        "trust_tier": "neutral",
        "email_count": 5,
        "total_approved": 2,
        "total_rejected": 1,
        "approval_rate": 0.67,
    }])
    assert "<img" not in result
    assert "&lt;img" in result


def test_corrections_table_html_escapes_email_id():
    """Corrections table should escape HTML characters in email_gmail_id."""
    payload = "<img src=x onerror=alert(1)>"
    result = corrections_table_html([{
        "created_at": 1234567890,
        "email_gmail_id": payload,
        "original_label": "WORK",
        "corrected_label": "WORK",
        "source": "dashboard",
    }])
    assert "<img" not in result
    assert "&lt;img" in result


def test_corrections_table_html_escapes_source():
    """Corrections table should escape HTML characters in source."""
    payload = "<img src=x onerror=alert(1)>"
    result = corrections_table_html([{
        "created_at": 1234567890,
        "email_gmail_id": "abc123",
        "original_label": "WORK",
        "corrected_label": "WORK",
        "source": payload,
    }])
    assert "<img" not in result
    assert "&lt;img" in result
