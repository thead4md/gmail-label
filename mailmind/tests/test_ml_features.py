"""Tests for ML feature extraction."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from mailmind.storage.models import Email
from mailmind.ml.features import (
    extract_features,
    FeatureVector,
    _detect_unsubscribe,
    _detect_calendar,
    _detect_finance,
    feature_vector_to_dict,
)


def _make_email(
    gmail_id: str = "test123",
    sender: str = "alice@example.com",
    subject: str = "Hello",
    snippet: str = "Quick question",
    body_text: str = "Just checking in",
    recipients=None,
    date_ts: int = int(datetime.now(timezone.utc).timestamp()),
    labels=None,
) -> Email:
    return Email(
        gmail_id=gmail_id,
        sender=sender,
        subject=subject,
        snippet=snippet,
        body_text=body_text,
        recipients=recipients or ["me@example.com"],
        date_ts=date_ts,
        labels=labels or [],
        parsed=True,
    )


class TestFeatureExtraction:
    """Test basic feature extraction from Email models."""

    def test_extract_basic_email(self):
        """Test feature extraction from a simple email."""
        email = _make_email()
        fv = extract_features(email)
        assert isinstance(fv, FeatureVector)
        assert fv.subject == "Hello"
        assert fv.sender == "alice@example.com"
        assert fv.sender_domain == "example.com"
        assert fv.sender_local == "alice"
        assert fv.num_recipients == 1
        assert fv.recency_hours is not None

    def test_extract_no_sender(self):
        """Test extraction handles missing sender."""
        email = _make_email(sender=None)
        fv = extract_features(email)
        assert fv.sender == ""
        assert fv.sender_domain == ""
        assert fv.sender_local == ""

    def test_extract_no_date(self):
        """Test extraction handles missing date."""
        email = _make_email(date_ts=None)
        fv = extract_features(email)
        assert fv.recency_hours is None

    def test_extract_unsubscribe_signal(self):
        """Test unsubscribe signal detection in body."""
        email = _make_email(body_text="Click here to unsubscribe from our newsletter")
        fv = extract_features(email)
        assert fv.has_unsubscribe_signal is True

    def test_extract_no_unsubscribe_signal(self):
        """Test no false positive on unsubscribe."""
        email = _make_email(body_text="Thanks for your order!")
        fv = extract_features(email)
        assert fv.has_unsubscribe_signal is False

    def test_extract_calendar_signal(self):
        """Test calendar signal detection."""
        email = _make_email(subject="Meeting invitation: Project review")
        fv = extract_features(email)
        assert fv.has_calendar_signal is True

    def test_extract_finance_signal(self):
        """Test finance signal detection."""
        email = _make_email(body_text="Your invoice for $49.99 is ready")
        fv = extract_features(email)
        assert fv.has_finance_signal is True

    def test_mass_cc_detection(self):
        """Test mass CC detection with many recipients."""
        recipients = [f"user{i}@example.com" for i in range(10)]
        email = _make_email(recipients=recipients)
        fv = extract_features(email)
        assert fv.is_mass_cc is True

    def test_to_text_corpus(self):
        """Test text corpus generation combines fields."""
        email = _make_email()
        fv = extract_features(email)
        corpus = fv.to_text_corpus()
        assert "Hello" in corpus
        assert "Quick question" in corpus
        assert "alice@example.com" in corpus

    def test_true_label_passthrough(self):
        """Test that true_label is passed through."""
        email = _make_email()
        fv = extract_features(email, true_label="WORK")
        assert fv.true_label == "WORK"

    def test_feature_vector_to_dict(self):
        """Test conversion to dict for logging."""
        email = _make_email()
        fv = extract_features(email)
        d = feature_vector_to_dict(fv)
        assert isinstance(d, dict)
        assert d["subject"] == "Hello"
        assert d["sender_domain"] == "example.com"
        assert d["num_recipients"] == 1


class TestSignalDetectors:
    """Test standalone signal detection functions."""

    def test_detect_unsubscribe_variants(self):
        assert _detect_unsubscribe("unsubscribe here") is True
        assert _detect_unsubscribe("List-Unsubscribe: <url>") is True
        assert _detect_unsubscribe("Manage your subscriptions") is True
        assert _detect_unsubscribe("Click here to stop receiving these emails") is True
        assert _detect_unsubscribe("Just a normal email") is False
        assert _detect_unsubscribe(None) is False
        assert _detect_unsubscribe("") is False

    def test_detect_calendar_variants(self):
        assert _detect_calendar("You are invited to a meeting") is True
        assert _detect_calendar("Calendar event: Team sync") is True
        assert _detect_calendar("ICS attachment included") is True
        assert _detect_calendar("Just a normal email") is False
        assert _detect_calendar(None) is False

    def test_detect_finance_variants(self):
        assert _detect_finance("Payment received") is True
        assert _detect_finance("Your invoice is attached") is True
        assert _detect_finance("Receipt for your purchase") is True
        assert _detect_finance("Just a normal email") is False
        assert _detect_finance(None) is False
