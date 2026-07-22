"""Tests for the relationship graph / contact ranking (client-strategy
reframe §4.3): queries.get_contact_reciprocity and
intelligence/relationships.py.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mailmind.storage.database import Database
from mailmind.storage.queries import upsert_loop, close_loop, get_contact_reciprocity
from mailmind.intelligence.relationships import (
    score_contact, compute_contact_rank, get_contact_rank_map, VIP_THRESHOLD,
)

DAY = 86400


@pytest.fixture
def db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    database = Database(db_path)
    yield database
    database.close()
    db_path.unlink(missing_ok=True)


def _closed_loop(db, contact_email, last_sent_ts, closed_ts, account=None):
    lid = upsert_loop(db, account=account, thread_id=f"t-{contact_email}-{last_sent_ts}",
                       contact_email=contact_email, last_sent_ts=last_sent_ts, last_activity_ts=last_sent_ts)
    # Force updated_at to a specific "closed at" timestamp for a deterministic test.
    db.execute_sql("UPDATE loops SET state='closed', updated_at=? WHERE id=?", (closed_ts, lid))
    return lid


# --------------------------------------------------------------------------- #
# get_contact_reciprocity
# --------------------------------------------------------------------------- #
class TestGetContactReciprocity:
    def test_no_closed_loops_returns_empty(self, db):
        assert get_contact_reciprocity(db) == {}

    def test_open_loops_are_excluded(self, db):
        upsert_loop(db, account="me", thread_id="t1", contact_email="bob@y.com", last_sent_ts=0)
        assert get_contact_reciprocity(db) == {}

    def test_single_closed_loop_computes_days(self, db):
        _closed_loop(db, "bob@y.com", last_sent_ts=0, closed_ts=2 * DAY)
        result = get_contact_reciprocity(db)
        assert result == {"bob@y.com": 2.0}

    def test_averages_across_multiple_closed_loops(self, db):
        _closed_loop(db, "bob@y.com", last_sent_ts=0, closed_ts=1 * DAY)
        _closed_loop(db, "bob@y.com", last_sent_ts=100 * DAY, closed_ts=104 * DAY)
        result = get_contact_reciprocity(db)
        assert result == {"bob@y.com": 2.5}  # avg(1, 4)

    def test_account_filter(self, db):
        _closed_loop(db, "bob@y.com", last_sent_ts=0, closed_ts=1 * DAY, account="acct1")
        _closed_loop(db, "bob@y.com", last_sent_ts=0, closed_ts=10 * DAY, account="acct2")
        assert get_contact_reciprocity(db, account="acct1") == {"bob@y.com": 1.0}


# --------------------------------------------------------------------------- #
# score_contact (pure)
# --------------------------------------------------------------------------- #
class TestScoreContact:
    def _profile(self, **overrides):
        base = {"trust_tier": "neutral", "approval_rate": 0.0, "email_count": 0}
        base.update(overrides)
        return base

    def test_neutral_unknown_contact_scores_base(self):
        assert score_contact(self._profile(), None) == 50.0

    def test_trusted_tier_boosts_score(self):
        assert score_contact(self._profile(trust_tier="trusted"), None) == 80.0

    def test_watchlist_tier_lowers_score(self):
        assert score_contact(self._profile(trust_tier="watchlist"), None) == 20.0

    def test_high_approval_rate_boosts_score(self):
        assert score_contact(self._profile(approval_rate=1.0), None) == 80.0

    def test_fast_reciprocity_boosts_score(self):
        assert score_contact(self._profile(), 0.5) == 75.0

    def test_slow_reciprocity_penalizes_score(self):
        assert score_contact(self._profile(), 30.0) == 40.0

    def test_volume_component_capped(self):
        low = score_contact(self._profile(email_count=5), None)
        high = score_contact(self._profile(email_count=1000), None)
        assert high > low
        assert high == 60.0  # capped at 20 * 0.5 = +10

    def test_score_never_exceeds_100_or_below_0(self):
        maxed = score_contact(self._profile(trust_tier="trusted", approval_rate=1.0, email_count=1000), 0.1)
        assert maxed == 100.0
        mined = score_contact(self._profile(trust_tier="watchlist", approval_rate=0.0), 30.0)
        assert mined == 10.0


# --------------------------------------------------------------------------- #
# compute_contact_rank / get_contact_rank_map (integration)
# --------------------------------------------------------------------------- #
class TestComputeContactRank:
    def _seed_sender(self, db, sender_email, approved=0, rejected=0):
        with db.transaction() as cur:
            cur.execute(
                "INSERT INTO emails (gmail_id, sender) VALUES (?, ?)",
                (f"seed-{sender_email}", sender_email),
            )
            cur.execute(
                "INSERT INTO sender_profiles (sender_email, total_approved, total_rejected)"
                " VALUES (?, ?, ?)",
                (sender_email, approved, rejected),
            )

    def test_ranked_highest_score_first(self, db):
        self._seed_sender(db, "trusted@y.com", approved=10, rejected=0)
        self._seed_sender(db, "watchlist@y.com", approved=0, rejected=10)
        db.execute_sql("UPDATE sender_profiles SET trust_tier='trusted' WHERE sender_email=?", ("trusted@y.com",))
        db.execute_sql("UPDATE sender_profiles SET trust_tier='watchlist' WHERE sender_email=?", ("watchlist@y.com",))

        ranked = compute_contact_rank(db)
        emails = [r["sender_email"] for r in ranked]
        assert emails.index("trusted@y.com") < emails.index("watchlist@y.com")

    def test_vip_flag_uses_threshold(self, db):
        self._seed_sender(db, "vip@y.com", approved=10, rejected=0)
        db.execute_sql("UPDATE sender_profiles SET trust_tier='trusted' WHERE sender_email=?", ("vip@y.com",))
        ranked = compute_contact_rank(db)
        entry = next(r for r in ranked if r["sender_email"] == "vip@y.com")
        assert entry["rank_score"] >= VIP_THRESHOLD
        assert entry["vip"] is True

    def test_no_history_contact_is_not_vip(self, db):
        self._seed_sender(db, "nobody@y.com")
        ranked = compute_contact_rank(db)
        entry = next(r for r in ranked if r["sender_email"] == "nobody@y.com")
        assert entry["vip"] is False

    def test_get_contact_rank_map_keys_by_sender_email(self, db):
        self._seed_sender(db, "bob@y.com")
        rank_map = get_contact_rank_map(db)
        assert "bob@y.com" in rank_map
        # base 50 + tiny volume component from the one seeded email (1 * 0.5).
        assert rank_map["bob@y.com"]["rank_score"] == 50.5

    def test_respects_limit(self, db):
        for i in range(5):
            self._seed_sender(db, f"c{i}@y.com")
        assert len(compute_contact_rank(db, limit=2)) == 2
