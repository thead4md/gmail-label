"""Test sender/thread label rules (user-defined overrides)."""
from __future__ import annotations

import tempfile
import pathlib
from unittest.mock import MagicMock

import pytest

from mailmind.storage.database import Database
from mailmind.storage.models import Email
from mailmind.storage.queries import (
    set_sender_label_rule, set_thread_label_rule,
    get_sender_label, get_thread_label,
    log_correction, get_recent_corrections,
)
from mailmind.intelligence.feedback import handle_label_email
from mailmind.ml.classifier_router import ClassifierRouter
from mailmind.processing.rules import RulesEngine


@pytest.fixture
def temp_db():
    """Create a temporary in-memory database for testing."""
    with tempfile.TemporaryDirectory() as d:
        db = Database(pathlib.Path(d) / "test.db")
        yield db


def test_sender_label_rule_round_trip(temp_db):
    """Test set_sender_label_rule and get_sender_label."""
    sender = "alice@example.com"
    label = "WORK"
    account = "primary"

    # Initially no rule
    assert get_sender_label(temp_db, sender, account) is None

    # Set rule
    set_sender_label_rule(temp_db, sender, label, account=account)

    # Retrieve rule
    assert get_sender_label(temp_db, sender, account) == label


def test_thread_label_rule_round_trip(temp_db):
    """Test set_thread_label_rule and get_thread_label."""
    thread_id = "thread_123"
    label = "PERSONAL"

    # Initially no rule
    assert get_thread_label(temp_db, thread_id) is None

    # Set rule
    set_thread_label_rule(temp_db, thread_id, label)

    # Retrieve rule
    assert get_thread_label(temp_db, thread_id) == label


def test_sender_label_rule_no_account(temp_db):
    """Test sender rule lookup with no account (None)."""
    sender = "bob@example.com"
    label = "ARCHIVE"

    set_sender_label_rule(temp_db, sender, label, account=None)
    assert get_sender_label(temp_db, sender, account=None) == label


def test_router_uses_sender_rule(temp_db):
    """Test that ClassifierRouter returns source='rule' for sender rules."""
    rules_engine = MagicMock(spec=RulesEngine)
    rules_engine.evaluate.return_value = []  # No rule engine matches

    router = ClassifierRouter(
        rules_engine=rules_engine,
        rules_threshold=0.90,
    )

    sender = "charlie@example.com"
    label = "PERSONAL"
    account = "primary"

    # Set a sender rule
    set_sender_label_rule(temp_db, sender, label, account=account)

    # Create email that matches the rule
    email = Email(
        gmail_id="msg_456",
        sender=sender,
        thread_id="thread_456",
        subject="Test",
    )

    # Route through router with db and account
    result = router.route(email, rule_matches=[], db=temp_db, account=account)

    assert result.source == "rule"
    assert result.label == label
    assert result.confidence == 1.0


def test_router_uses_thread_rule(temp_db):
    """Test that ClassifierRouter returns source='rule' for thread rules."""
    rules_engine = MagicMock(spec=RulesEngine)
    rules_engine.evaluate.return_value = []

    router = ClassifierRouter(
        rules_engine=rules_engine,
        rules_threshold=0.90,
    )

    thread_id = "thread_789"
    label = "ARCHIVE"

    # Set a thread rule
    set_thread_label_rule(temp_db, thread_id, label)

    # Create email with matching thread_id
    email = Email(
        gmail_id="msg_789",
        sender="dave@example.com",
        thread_id=thread_id,
        subject="Test",
    )

    # Route with db (thread rule doesn't need account)
    result = router.route(email, rule_matches=[], db=temp_db)

    assert result.source == "rule"
    assert result.label == label


def test_router_prefers_thread_rule_over_sender_rule(temp_db):
    """Thread rules take precedence over sender rules."""
    rules_engine = MagicMock(spec=RulesEngine)
    rules_engine.evaluate.return_value = []

    router = ClassifierRouter(
        rules_engine=rules_engine,
        rules_threshold=0.90,
    )

    sender = "eve@example.com"
    thread_id = "thread_999"

    # Set both rules with different labels
    set_sender_label_rule(temp_db, sender, "WORK", account=None)
    set_thread_label_rule(temp_db, thread_id, "ARCHIVE")

    email = Email(
        gmail_id="msg_999",
        sender=sender,
        thread_id=thread_id,
        subject="Test",
    )

    result = router.route(email, rule_matches=[], db=temp_db, account=None)

    # Thread rule should win
    assert result.label == "ARCHIVE"


def test_handle_label_email_sender_scope(temp_db):
    """Test handle_label_email with sender scope."""
    # Insert test email
    email = Email(
        gmail_id="msg_001",
        sender="frank@example.com",
        thread_id="thread_001",
        subject="Test",
    )
    temp_db.insert_email(email)

    # Insert action queue item
    with temp_db.transaction() as cur:
        cur.execute(
            "INSERT INTO action_queue "
            "(email_gmail_id, action, status) VALUES (?, ?, ?)",
            ("msg_001", "label:WORK", "pending"),
        )
        queue_id = cur.lastrowid

    # Call handle_label_email with sender scope
    result = handle_label_email(
        temp_db,
        queue_id,
        label="PERSONAL",
        scope="sender",
        executor=None,
        account="primary",
    )

    assert result is True

    # Check rule was created
    assert get_sender_label(temp_db, "frank@example.com", account="primary") == "PERSONAL"

    # Check correction was logged
    corrections = get_recent_corrections(temp_db, limit=10)
    assert len(corrections) > 0
    assert corrections[0]["corrected_label"] == "PERSONAL"


def test_handle_label_email_thread_scope(temp_db):
    """Test handle_label_email with thread scope."""
    email = Email(
        gmail_id="msg_002",
        sender="grace@example.com",
        thread_id="thread_002",
        subject="Test",
    )
    temp_db.insert_email(email)

    with temp_db.transaction() as cur:
        cur.execute(
            "INSERT INTO action_queue "
            "(email_gmail_id, action, status) VALUES (?, ?, ?)",
            ("msg_002", "label:WORK", "pending"),
        )
        queue_id = cur.lastrowid

    # Call with thread scope
    result = handle_label_email(
        temp_db,
        queue_id,
        label="ARCHIVE",
        scope="thread",
        executor=None,
    )

    assert result is True
    assert get_thread_label(temp_db, "thread_002") == "ARCHIVE"


def test_handle_label_email_email_scope(temp_db):
    """Test handle_label_email with email scope (one-off correction)."""
    email = Email(
        gmail_id="msg_003",
        sender="henry@example.com",
        thread_id="thread_003",
        subject="Test",
    )
    temp_db.insert_email(email)

    with temp_db.transaction() as cur:
        cur.execute(
            "INSERT INTO action_queue "
            "(email_gmail_id, action, status) VALUES (?, ?, ?)",
            ("msg_003", "label:WORK", "pending"),
        )
        queue_id = cur.lastrowid

    # Call with email scope
    result = handle_label_email(
        temp_db,
        queue_id,
        label="SPAM",
        scope="email",
        executor=None,
    )

    assert result is True

    # No rule should be created for email scope
    assert get_sender_label(temp_db, "henry@example.com", account=None) is None
    assert get_thread_label(temp_db, "thread_003") is None

    # But correction should be logged
    corrections = get_recent_corrections(temp_db, limit=10)
    assert any(c["corrected_label"] == "SPAM" for c in corrections)


def test_handle_label_email_nonexistent_queue_id(temp_db):
    """Test handle_label_email with nonexistent queue_id."""
    result = handle_label_email(
        temp_db,
        queue_id=99999,
        label="WORK",
        scope="sender",
        executor=None,
    )

    assert result is False
