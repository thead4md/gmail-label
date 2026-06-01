from __future__ import annotations

import tempfile
import time
from pathlib import Path
from mailmind.storage.database import Database
from mailmind.storage.models import Email, QueueItem, Prediction
from mailmind.intelligence.feedback import handle_approve, handle_reject, handle_correction
from mailmind.storage.queries import (
    upsert_queue_item,
    get_queue_item_by_fingerprint,
    get_sender_profiles,
    get_queue_stats,
    get_recent_corrections,
)
from mailmind.utils.fingerprint import make_action_fingerprint


def setup_db():
    tmpdir = tempfile.TemporaryDirectory()
    db = Database(Path(tmpdir.name)/'test.db')
    return db, tmpdir


def test_handle_approve_updates_status_and_sender_profile():
    """Test that handle_approve updates queue status and sender profile."""
    db, tmpdir = setup_db()
    try:
        # insert email
        email = Email(gmail_id='a1', sender='x@example.com')
        db.insert_email(email)
        fp = make_action_fingerprint(email.gmail_id, 'star', {})
        qi = QueueItem(email_gmail_id=email.gmail_id, action='star', action_fingerprint=fp, status='pending')
        q = upsert_queue_item(db, qi)
        assert q is not None

        # Approve the queue item
        handle_approve(db, q.id)

        # Verify status changed to 'approved'
        row = db.execute_sql('SELECT status, reviewed_at FROM action_queue WHERE id=?', (q.id,)).fetchone()
        assert row['status'] == 'approved'
        assert row['reviewed_at'] is not None

        # Verify sender_profiles was updated
        profiles = get_sender_profiles(db)
        profile = next((p for p in profiles if p['sender_email'] == email.sender), None)
        assert profile is not None
        assert profile['total_approved'] == 1
    finally:
        tmpdir.cleanup()


def test_handle_reject_updates_status_and_sender_profile():
    """Test that handle_reject updates queue status and sender profile."""
    db, tmpdir = setup_db()
    try:
        email = Email(gmail_id='a2', sender='y@example.com')
        db.insert_email(email)
        fp = make_action_fingerprint(email.gmail_id, 'archive', {})
        qi = QueueItem(email_gmail_id=email.gmail_id, action='archive', action_fingerprint=fp, status='pending')
        q = upsert_queue_item(db, qi)

        # Reject the queue item
        handle_reject(db, q.id)

        # Verify status changed to 'rejected'
        row = db.execute_sql('SELECT status, reviewed_at FROM action_queue WHERE id=?', (q.id,)).fetchone()
        assert row['status'] == 'rejected'
        assert row['reviewed_at'] is not None

        # Verify sender profile was updated
        profiles = get_sender_profiles(db)
        profile = next((p for p in profiles if p['sender_email'] == email.sender), None)
        assert profile is not None
        assert profile['total_rejected'] == 1
    finally:
        tmpdir.cleanup()


def test_handle_reject_with_corrected_action_logs_correction():
    """Test that handle_reject with corrected_action logs a correction."""
    db, tmpdir = setup_db()
    try:
        email = Email(gmail_id='a3', sender='z@example.com')
        db.insert_email(email)
        fp = make_action_fingerprint(email.gmail_id, 'archive', {})
        qi = QueueItem(email_gmail_id=email.gmail_id, action='archive', action_fingerprint=fp, status='pending')
        q = upsert_queue_item(db, qi)

        # Reject with corrected action
        handle_reject(db, q.id, corrected_action='label:WORK')

        # Verify status changed
        row = db.execute_sql('SELECT status FROM action_queue WHERE id=?', (q.id,)).fetchone()
        assert row['status'] == 'rejected'

        # Verify correction was logged
        corrections = get_recent_corrections(db, limit=100)
        assert len(corrections) > 0
        latest = corrections[0]
        assert latest['original_action'] == 'archive'
        assert latest['corrected_action'] == 'label:WORK'
    finally:
        tmpdir.cleanup()


def test_handle_correction_logs_corrected_label():
    """Test that handle_correction logs corrected label."""
    db, tmpdir = setup_db()
    try:
        # Setup email and queue item
        email = Email(gmail_id='a4', sender='w@example.com')
        db.insert_email(email)

        pred = Prediction(
            email_gmail_id='a4',
            model='rules',
            labels=['important'],
            priority_score=75,
            primary_label='important',
            confidence=0.85,
        )
        db.save_prediction(pred)

        fp = make_action_fingerprint(email.gmail_id, 'label', {})
        qi = QueueItem(email_gmail_id=email.gmail_id, action='label', action_fingerprint=fp, status='pending')
        q = upsert_queue_item(db, qi)

        # Apply correction
        handle_correction(db, q.id, corrected_label='spam')

        # Verify correction was logged
        corrections = get_recent_corrections(db, limit=100)
        assert len(corrections) > 0
        latest = corrections[0]
        assert latest['corrected_label'] == 'spam'
    finally:
        tmpdir.cleanup()


def test_handle_approve_gracefully_handles_missing_queue_item():
    """Test that handle_approve gracefully handles missing queue item."""
    db, tmpdir = setup_db()
    try:
        # Try to approve non-existent queue item - should not raise
        handle_approve(db, 99999)
        # If we get here, no exception was raised
        assert True
    finally:
        tmpdir.cleanup()


def test_handle_reject_gracefully_handles_missing_queue_item():
    """Test that handle_reject gracefully handles missing queue item."""
    db, tmpdir = setup_db()
    try:
        # Try to reject non-existent queue item - should not raise
        handle_reject(db, 99999)
        # If we get here, no exception was raised
        assert True
    finally:
        tmpdir.cleanup()


def test_handle_correction_gracefully_handles_missing_queue_item():
    """Test that handle_correction gracefully handles missing queue item."""
    db, tmpdir = setup_db()
    try:
        # Try to correct non-existent queue item - should not raise
        handle_correction(db, 99999, corrected_label='spam')
        # If we get here, no exception was raised
        assert True
    finally:
        tmpdir.cleanup()


# ---------------------------------------------------------------------------
# Bool return value tests (race-condition guard)
# ---------------------------------------------------------------------------

def test_handle_approve_returns_true_for_existing_item():
    db, tmpdir = setup_db()
    try:
        email = Email(gmail_id='b1', sender='ok@example.com')
        db.insert_email(email)
        fp = make_action_fingerprint(email.gmail_id, 'star', {})
        qi = QueueItem(email_gmail_id=email.gmail_id, action='star', action_fingerprint=fp, status='pending')
        q = upsert_queue_item(db, qi)
        result = handle_approve(db, q.id)
        assert result is True
    finally:
        tmpdir.cleanup()


def test_handle_approve_returns_false_for_missing_item():
    db, tmpdir = setup_db()
    try:
        result = handle_approve(db, 99999)
        assert result is False
    finally:
        tmpdir.cleanup()


def test_handle_reject_returns_true_for_existing_item():
    db, tmpdir = setup_db()
    try:
        email = Email(gmail_id='b2', sender='ok2@example.com')
        db.insert_email(email)
        fp = make_action_fingerprint(email.gmail_id, 'archive', {})
        qi = QueueItem(email_gmail_id=email.gmail_id, action='archive', action_fingerprint=fp, status='pending')
        q = upsert_queue_item(db, qi)
        result = handle_reject(db, q.id)
        assert result is True
    finally:
        tmpdir.cleanup()


def test_handle_reject_returns_false_for_missing_item():
    db, tmpdir = setup_db()
    try:
        result = handle_reject(db, 99999)
        assert result is False
    finally:
        tmpdir.cleanup()


def test_handle_correction_returns_true_for_existing_item():
    db, tmpdir = setup_db()
    try:
        email = Email(gmail_id='b3', sender='ok3@example.com')
        db.insert_email(email)
        fp = make_action_fingerprint(email.gmail_id, 'label', {})
        qi = QueueItem(email_gmail_id=email.gmail_id, action='label', action_fingerprint=fp, status='pending')
        q = upsert_queue_item(db, qi)
        result = handle_correction(db, q.id, corrected_label='SPAM')
        assert result is True
    finally:
        tmpdir.cleanup()


def test_handle_correction_returns_false_for_missing_item():
    db, tmpdir = setup_db()
    try:
        result = handle_correction(db, 99999, corrected_label='SPAM')
        assert result is False
    finally:
        tmpdir.cleanup()


def test_queue_stats_correctly_counts_items():
    """Test that queue stats correctly counts items by status."""
    db, tmpdir = setup_db()
    try:
        # Create a pending item
        email = Email(gmail_id='stats1', sender='stats@example.com')
        db.insert_email(email)
        fp = make_action_fingerprint(email.gmail_id, 'label', {})
        qi = QueueItem(email_gmail_id=email.gmail_id, action='label', action_fingerprint=fp, status='pending')
        q = upsert_queue_item(db, qi)

        # Check initial stats
        stats = get_queue_stats(db)
        assert stats['pending'] == 1

        # Approve the item
        handle_approve(db, q.id)

        # Check updated stats
        stats = get_queue_stats(db)
        assert stats['approved'] == 1
        assert stats['pending'] == 0
    finally:
        tmpdir.cleanup()


def test_sender_profile_tracks_approval_rate():
    """Test that sender profile correctly calculates approval rate."""
    db, tmpdir = setup_db()
    try:
        sender = 'rate@example.com'

        # Create and approve first item
        email1 = Email(gmail_id='rate1', sender=sender)
        db.insert_email(email1)
        fp1 = make_action_fingerprint(email1.gmail_id, 'label', {})
        qi1 = QueueItem(email_gmail_id=email1.gmail_id, action='label', action_fingerprint=fp1, status='pending')
        q1 = upsert_queue_item(db, qi1)
        handle_approve(db, q1.id)

        profiles = get_sender_profiles(db)
        profile = next((p for p in profiles if p['sender_email'] == sender), None)
        assert profile is not None
        assert profile['total_approved'] == 1
        assert profile['total_rejected'] == 0
        assert profile['approval_rate'] == 1.0

        # Create and reject second item
        email2 = Email(gmail_id='rate2', sender=sender)
        db.insert_email(email2)
        fp2 = make_action_fingerprint(email2.gmail_id, 'archive', {})
        qi2 = QueueItem(email_gmail_id=email2.gmail_id, action='archive', action_fingerprint=fp2, status='pending')
        q2 = upsert_queue_item(db, qi2)
        handle_reject(db, q2.id)

        # Check updated profile
        profiles = get_sender_profiles(db)
        profile = next((p for p in profiles if p['sender_email'] == sender), None)
        assert profile is not None
        assert profile['total_approved'] == 1
        assert profile['total_rejected'] == 1
        assert profile['approval_rate'] == 0.5
    finally:
        tmpdir.cleanup()

