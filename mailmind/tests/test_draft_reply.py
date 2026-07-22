"""Tests for AI-drafted replies (mailmind/intelligence/draft_reply.py).

All LLM calls are mocked — no real API calls are made, per project convention.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mailmind.intelligence.draft_reply import draft_reply
from mailmind.storage.database import Database


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def _mock_deepseek_shaped_client(content="Thanks for reaching out, I'll get back to you soon."):
    """A MagicMock shaped like DeepSeekClient: .client.chat.completions.create(...)
    returning an OpenAI-style response with .choices[0].message.content and
    .usage.prompt_tokens/.completion_tokens."""
    client = MagicMock()
    client.model = "deepseek-chat"
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    response.usage = MagicMock(prompt_tokens=120, completion_tokens=40)
    client.client.chat.completions.create.return_value = response
    return client, response


class TestDraftReply:
    def test_successful_draft_generation(self, db):
        llm_client, response = _mock_deepseek_shaped_client("Sure, I can meet Tuesday at 3pm.")
        email = {
            "subject": "Meeting request",
            "sender": "alice@example.com",
            "body_text": "Can we meet next week to discuss the budget?",
        }
        result = draft_reply(db, llm_client, email)
        assert result == "Sure, I can meet Tuesday at 3pm."
        llm_client.client.chat.completions.create.assert_called_once()

    def test_llm_client_none_returns_none(self, db):
        email = {"subject": "hi", "sender": "a@b.com", "body_text": "test"}
        assert draft_reply(db, None, email) is None

    def test_daily_cost_cap_blocks_generation(self, db):
        llm_client, _ = _mock_deepseek_shaped_client()
        email = {"subject": "hi", "sender": "a@b.com", "body_text": "test"}
        with patch(
            "mailmind.storage.queries.analytics_llm_cost",
            return_value={"cost_usd": 0.75},
        ) as mock_cost:
            result = draft_reply(db, llm_client, email, daily_cost_cap_usd=0.50)
        assert result is None
        mock_cost.assert_called_once()
        llm_client.client.chat.completions.create.assert_not_called()

    def test_cost_exactly_at_cap_blocks_generation(self, db):
        """A hard cap: spent == cap must also refuse (not just spent > cap)."""
        llm_client, _ = _mock_deepseek_shaped_client()
        email = {"subject": "hi", "sender": "a@b.com", "body_text": "test"}
        with patch(
            "mailmind.storage.queries.analytics_llm_cost",
            return_value={"cost_usd": 0.50},
        ):
            result = draft_reply(db, llm_client, email, daily_cost_cap_usd=0.50)
        assert result is None
        llm_client.client.chat.completions.create.assert_not_called()

    def test_llm_call_exception_returns_none_gracefully(self, db):
        llm_client = MagicMock()
        llm_client.model = "deepseek-chat"
        llm_client.client.chat.completions.create.side_effect = RuntimeError("network error")
        email = {"subject": "hi", "sender": "a@b.com", "body_text": "test"}
        result = draft_reply(db, llm_client, email)
        assert result is None

    def test_empty_llm_response_returns_none(self, db):
        llm_client, _ = _mock_deepseek_shaped_client(content="")
        email = {"subject": "hi", "sender": "a@b.com", "body_text": "test"}
        result = draft_reply(db, llm_client, email)
        assert result is None

    def test_usage_is_recorded_via_llm_usage_table(self, db):
        llm_client, _ = _mock_deepseek_shaped_client("A reply.")
        email = {"subject": "hi", "sender": "a@b.com", "body_text": "test"}
        result = draft_reply(db, llm_client, email)
        assert result == "A reply."
        row = db.execute_sql(
            "SELECT * FROM llm_usage WHERE kind = 'draft_reply'"
        ).fetchone()
        assert row is not None
        assert row["prompt_tokens"] == 120
        assert row["completion_tokens"] == 40

    def test_hungarian_email_requests_hungarian_reply(self, db):
        llm_client, _ = _mock_deepseek_shaped_client("Köszönöm, viszlát!")
        email = {
            "subject": "Üdvözlet",
            "sender": "kovacs@cserkesz.hu",
            "body_text": "Kedves Ádám, köszönöm a gyors választ. Üdvözlettel, Kovács.",
        }
        draft_reply(db, llm_client, email)
        call_kwargs = llm_client.client.chat.completions.create.call_args.kwargs
        system_msg = call_kwargs["messages"][0]["content"]
        assert "Hungarian" in system_msg

    def test_english_email_requests_english_reply(self, db):
        llm_client, _ = _mock_deepseek_shaped_client("Sounds good, thanks!")
        email = {
            "subject": "Quick question",
            "sender": "bob@example.com",
            "body_text": "Hey, do you have five minutes today?",
        }
        draft_reply(db, llm_client, email)
        call_kwargs = llm_client.client.chat.completions.create.call_args.kwargs
        system_msg = call_kwargs["messages"][0]["content"]
        assert "English" in system_msg

    def test_action_items_and_deadlines_included_in_prompt(self, db):
        llm_client, _ = _mock_deepseek_shaped_client("Noted.")
        email = {
            "subject": "Project update",
            "sender": "carol@example.com",
            "body_text": "Please review the attached doc.",
            "action_items": ["Review the budget doc"],
            "deadlines": ["Friday EOD"],
        }
        draft_reply(db, llm_client, email)
        call_kwargs = llm_client.client.chat.completions.create.call_args.kwargs
        user_msg = call_kwargs["messages"][1]["content"]
        assert "Review the budget doc" in user_msg
        assert "Friday EOD" in user_msg

    def test_thread_summary_included_when_provided(self, db):
        llm_client, _ = _mock_deepseek_shaped_client("Sure.")
        email = {"subject": "Re: Trip", "sender": "dave@example.com", "body_text": "..."}
        draft_reply(db, llm_client, email, thread_summary="Planning a summer camp trip.")
        call_kwargs = llm_client.client.chat.completions.create.call_args.kwargs
        user_msg = call_kwargs["messages"][1]["content"]
        assert "Planning a summer camp trip." in user_msg

    def test_body_truncated_to_500_chars(self, db):
        llm_client, _ = _mock_deepseek_shaped_client("ok")
        long_body = "x" * 2000
        email = {"subject": "long", "sender": "a@b.com", "body_text": long_body}
        draft_reply(db, llm_client, email)
        call_kwargs = llm_client.client.chat.completions.create.call_args.kwargs
        user_msg = call_kwargs["messages"][1]["content"]
        assert "x" * 501 not in user_msg
        assert "x" * 500 in user_msg

    def test_voice_examples_included_when_history_exists(self, db):
        from mailmind.storage.models import Email

        db.insert_email(Email(
            gmail_id="sent1", thread_id="t1", sender="me@x.com", recipients=["alice@example.com"],
            subject="s", snippet="s", body_text="Sure, sounds great, see you then!", date_ts=100,
            labels=["SENT"],
        ))
        llm_client, _ = _mock_deepseek_shaped_client("Sounds good.")
        email = {"subject": "Meeting", "sender": "alice@example.com", "body_text": "Can we meet?"}
        draft_reply(db, llm_client, email)
        call_kwargs = llm_client.client.chat.completions.create.call_args.kwargs
        user_msg = call_kwargs["messages"][1]["content"]
        assert "Sure, sounds great, see you then!" in user_msg
        assert "how I've written to this person before" in user_msg

    def test_no_voice_examples_block_when_no_history(self, db):
        llm_client, _ = _mock_deepseek_shaped_client("A reply.")
        email = {"subject": "hi", "sender": "nobody-seen-before@example.com", "body_text": "test"}
        draft_reply(db, llm_client, email)
        call_kwargs = llm_client.client.chat.completions.create.call_args.kwargs
        user_msg = call_kwargs["messages"][1]["content"]
        assert "how I've written to this person before" not in user_msg
