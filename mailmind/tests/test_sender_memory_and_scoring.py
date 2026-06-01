from __future__ import annotations

import tempfile
from pathlib import Path
import pytest
from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction
from mailmind.intelligence.sender_memory import get_sender_profile, get_sender_trust_tier, update_from_outcome
from mailmind.processing.scorer import PriorityScorer


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = Database(db_path)
        yield db
        db.close()


def test_sender_profile_and_trust_tier(db):
    # initial no profile
    assert get_sender_profile(db, 'bob@example.com') is None
    # simulate outcomes to create profile
    from mailmind.storage.queries import update_sender_profile
    for _ in range(5):
        update_sender_profile(db, 'bob@example.com', 'approved')
    sp = get_sender_profile(db, 'bob@example.com')
    assert sp is not None
    assert sp.trust_tier == 'trusted'


def test_scorer_nudge_from_sender_memory(db):
    # create email and prediction
    email = Email(gmail_id='msg_101', sender='bob@example.com', subject='Hey', snippet='Hi')
    db.insert_email(email)
    # ensure sender is trusted
    from mailmind.storage.queries import update_sender_profile
    for _ in range(5):
        update_sender_profile(db, 'bob@example.com', 'approved')

    scorer = PriorityScorer()
    score = scorer.compute_score(email, [], db=db)
    # trusted gives a small positive nudge; ensure sender_trust in result reflects nudge
    assert score.sender_trust >= 5 or score.total_score >= 5


def test_watchlist_tier_and_score_nudge(db):
    """Watchlist sender receives a negative score nudge from sender memory."""
    from mailmind.storage.queries import update_sender_profile

    email = Email(gmail_id='msg_watch', sender='bad@example.com', subject='Junk', snippet='')
    db.insert_email(email)

    # 3 rejections + 2 approvals → rejection_rate=0.6 > 0.5, total_seen=5 → watchlist
    for _ in range(3):
        update_sender_profile(db, 'bad@example.com', 'rejected')
    for _ in range(2):
        update_sender_profile(db, 'bad@example.com', 'approved')

    sp = get_sender_profile(db, 'bad@example.com')
    assert sp is not None
    assert sp.trust_tier == 'watchlist'

    scorer = PriorityScorer()
    score = scorer.compute_score(email, [], db=db)
    # watchlist gives -8 memory nudge; sender_trust absorbs it
    assert score.sender_trust <= -8


def test_neutral_sender_no_memory_nudge(db):
    """Sender with no profile receives zero memory nudge from scorer."""
    email = Email(gmail_id='msg_new', sender='unknown@example.com', subject='Hi', snippet='')
    db.insert_email(email)

    scorer = PriorityScorer()
    score = scorer.compute_score(email, [], db=db)
    # No profile → trust_tier='neutral' → memory_nudge=0
    assert score.sender_trust == 0

