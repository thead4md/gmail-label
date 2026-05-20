"""Unit tests for the PriorityScorer."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from mailmind.storage.models import Email
from mailmind.processing.scorer import PriorityScorer
from mailmind.processing.rules import RuleMatch


class TestPriorityScorer(unittest.TestCase):
    """Test suite for PriorityScorer."""

    def setUp(self):
        """Set up a default scorer for tests."""
        self.scorer = PriorityScorer(user_email="")

    def test_base_score_notification(self):
        """Emails with no special labels get a default NOTIFICATION score."""
        email = Email(gmail_id="test1", labels=["INBOX"])
        score = self.scorer.compute_score(email, [])
        self.assertEqual(score.primary_label, "NOTIFICATION")
        self.assertEqual(score.total_score, 30)  # Default base score

    def test_label_priority(self):
        """A higher-priority label like WORK should override NOTIFICATION."""
        email = Email(gmail_id="test2", labels=["INBOX", "WORK"])
        score = self.scorer.compute_score(email, [])
        self.assertEqual(score.primary_label, "WORK")
        self.assertEqual(score.total_score, 60)

    def test_rule_contribution(self):
        """Positive rule deltas should increase the score."""
        email = Email(gmail_id="test3", labels=["INBOX"])
        matches = [RuleMatch("important_sender", True, score_delta=20)]
        score = self.scorer.compute_score(email, matches)
        self.assertEqual(score.rule_contribution, 20)
        self.assertEqual(score.total_score, 30 + 20)

    def test_negative_rule_contribution(self):
        """Negative rule deltas should decrease the score."""
        email = Email(gmail_id="test4", labels=["INBOX"])
        matches = [RuleMatch("spammy_keyword", True, score_delta=-15)]
        score = self.scorer.compute_score(email, matches)
        self.assertEqual(score.rule_contribution, -15)
        self.assertEqual(score.total_score, 30 - 15)

    def test_recency_bonus(self):
        """Recent emails should get a recency bonus."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        email = Email(gmail_id="test5", labels=["INBOX"], date_ts=now_ts)
        score = self.scorer.compute_score(email, [])
        self.assertEqual(score.recency_bonus, 5)
        self.assertEqual(score.total_score, 30 + 5)

    def test_no_recency_bonus_for_old_emails(self):
        """Old emails should not get a recency bonus."""
        old_ts = int((datetime.now(timezone.utc) - timedelta(days=3)).timestamp())
        email = Email(gmail_id="test6", labels=["INBOX"], date_ts=old_ts)
        score = self.scorer.compute_score(email, [])
        self.assertEqual(score.recency_bonus, 0)
        self.assertEqual(score.total_score, 30)

    def test_score_clamping(self):
        """Scores should be clamped between 0 and 100."""
        # Test upper clamp
        email_high = Email(gmail_id="test7", labels=["URGENT"])
        matches_high = [RuleMatch("ceo_sender", True, score_delta=50)]
        score_high = self.scorer.compute_score(email_high, matches_high)
        self.assertEqual(score_high.total_score, 100)

        # Test lower clamp
        email_low = Email(gmail_id="test8", labels=["SPAMCANDIDATE"])
        matches_low = [RuleMatch("bad_keyword", True, score_delta=-20)]
        score_low = self.scorer.compute_score(email_low, matches_low)
        self.assertEqual(score_low.total_score, 0)

    def test_direct_mention_bonus_applied(self):
        """A bonus should be applied if the user's email is in recipients."""
        scorer = PriorityScorer(user_email="adam@example.com")
        email = Email(
            gmail_id="test9",
            labels=["INBOX"],
            recipients=["Some List <list@example.com>", "Adam Dudas <adam@example.com>"],
        )
        score = scorer.compute_score(email, [])
        self.assertEqual(score.direct_mention_bonus, 30)
        # 30 (base) + 30 (direct) = 60
        self.assertEqual(score.total_score, 60)

    def test_direct_mention_bonus_not_applied(self):
        """No bonus if user's email is not in recipients."""
        scorer = PriorityScorer(user_email="other@example.com")
        email = Email(
            gmail_id="test10",
            labels=["INBOX"],
            recipients=["Some List <list@example.com>", "Adam Dudas <adam@example.com>"],
        )
        score = scorer.compute_score(email, [])
        self.assertEqual(score.direct_mention_bonus, 0)
        self.assertEqual(score.total_score, 30) # Just the base score

    def test_no_user_email_no_bonus(self):
        """If scorer is initialized with no email, no bonus is applied."""
        scorer = PriorityScorer(user_email="") # Default
        email = Email(
            gmail_id="test11",
            labels=["INBOX"],
            recipients=["Adam Dudas <adam@example.com>"],
        )
        score = scorer.compute_score(email, [])
        self.assertEqual(score.direct_mention_bonus, 0)
        self.assertEqual(score.total_score, 30)

if __name__ == "__main__":
    unittest.main()
