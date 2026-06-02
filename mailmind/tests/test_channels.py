"""Tests for channel detection heuristics (intelligence/channels.py)."""
from __future__ import annotations

import pytest
from mailmind.intelligence.channels import detect_channel


# ---------------------------------------------------------------------------
# Newsletter detection
# ---------------------------------------------------------------------------

def test_unsubscribe_body_flags_newsletter():
    ch = detect_channel(
        subject="Weekly digest",
        sender="news@substack.com",
        body_text="Click here to unsubscribe from this newsletter.",
    )
    assert ch == "newsletter"


def test_newsletter_sender_domain():
    ch = detect_channel(
        subject="Your weekly roundup",
        sender="hello@mailchimp.com",
        body_text="Hope you enjoy this week's content.",
    )
    assert ch == "newsletter"


def test_opt_out_phrase_flags_newsletter():
    ch = detect_channel(
        subject="Updates",
        sender="updates@blog.com",
        body_text="If you wish to opt out of future emails click here.",
    )
    assert ch == "newsletter"


# ---------------------------------------------------------------------------
# Transactional detection
# ---------------------------------------------------------------------------

def test_order_subject_is_transactional():
    ch = detect_channel(
        subject="Your order #1234 has shipped",
        sender="shipping@shop.com",
        body_text="Your package is on its way.",
    )
    assert ch == "transactional"


def test_noreply_sender_is_transactional():
    ch = detect_channel(
        subject="Security alert",
        sender="no-reply@bank.com",
        body_text="A new sign-in was detected on your account.",
    )
    assert ch == "transactional"


def test_password_reset_is_transactional():
    ch = detect_channel(
        subject="Reset your password",
        sender="support@service.com",
        body_text="Click the link to reset your password.",
    )
    assert ch == "transactional"


def test_invoice_subject_is_transactional():
    ch = detect_channel(
        subject="Invoice #456 from Acme Corp",
        sender="billing@acme.com",
        body_text="Please find your invoice attached.",
    )
    assert ch == "transactional"


# ---------------------------------------------------------------------------
# Automated detection
# ---------------------------------------------------------------------------

def test_build_failed_is_automated():
    ch = detect_channel(
        subject="[GitHub Actions] Build failed — main",
        sender="noreply@github.com",
        body_text="Your workflow run failed.",
    )
    assert ch == "automated"


def test_monitoring_alert_is_automated():
    ch = detect_channel(
        subject="[Alert] Server CPU > 90%",
        sender="alerts@datadog.com",
        body_text="CPU utilisation exceeded threshold.",
    )
    assert ch == "automated"


def test_deploy_subject_is_automated():
    ch = detect_channel(
        subject="Deploy succeeded on production",
        sender="ci@circleci.com",
        body_text="Your deployment to production completed.",
    )
    assert ch == "automated"


# ---------------------------------------------------------------------------
# Marketing detection
# ---------------------------------------------------------------------------

def test_sale_subject_is_marketing():
    ch = detect_channel(
        subject="50% off — today only!",
        sender="promos@shop.com",
        body_text="Shop now and save big on our summer collection.",
    )
    assert ch == "marketing"


def test_flash_sale_is_marketing():
    ch = detect_channel(
        subject="Flash sale: limited time offer",
        sender="deals@company.com",
        body_text="Don't miss our exclusive deal.",
    )
    assert ch == "marketing"


# ---------------------------------------------------------------------------
# Team detection
# ---------------------------------------------------------------------------

def test_same_domain_is_team():
    ch = detect_channel(
        subject="Team standup notes",
        sender="alice@company.com",
        body_text="Here are yesterday's standup notes.",
        user_domain="company.com",
    )
    assert ch == "team"


def test_different_domain_not_team():
    ch = detect_channel(
        subject="Hello",
        sender="bob@other.com",
        body_text="Just checking in.",
        user_domain="company.com",
    )
    assert ch != "team"


def test_no_user_domain_skips_team():
    ch = detect_channel(
        subject="Meeting request",
        sender="alice@company.com",
        body_text="Can we meet tomorrow?",
        user_domain=None,
    )
    # Without user_domain, team detection is skipped
    assert ch in ("personal", "unknown")


# ---------------------------------------------------------------------------
# Personal / unknown
# ---------------------------------------------------------------------------

def test_personal_human_email():
    ch = detect_channel(
        subject="Are you free for lunch?",
        sender="friend@gmail.com",
        body_text="Hey, want to grab lunch on Friday?",
    )
    assert ch == "personal"


def test_empty_fields_returns_unknown_or_personal():
    ch = detect_channel(subject=None, sender=None, body_text=None)
    assert ch in ("unknown", "personal")


def test_priority_automated_over_transactional():
    """[Alert] subject should be automated even if sender looks transactional."""
    ch = detect_channel(
        subject="[Alert] Payment gateway error",
        sender="noreply@payments.com",
        body_text="Payment service returned 503.",
    )
    assert ch == "automated"
