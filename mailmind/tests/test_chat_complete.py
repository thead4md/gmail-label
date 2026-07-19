"""Tests for mailmind/llm/chat.py's chat_complete(), including the
return_usage extension added for AI-drafted replies (mailmind/intelligence/
draft_reply.py). All LLM calls are mocked.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from mailmind.llm.chat import chat_complete


def _deepseek_shaped_client(content="hello"):
    client = MagicMock()
    client.model = "deepseek-chat"
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    client.client.chat.completions.create.return_value = response
    return client, response


def _openai_adapter_shaped_client(content="hello", monkeypatch_openai=None):
    adapter = MagicMock(spec=["classifier"])
    adapter.classifier = MagicMock(api_key="sk-test", model="gpt-4o-mini")
    return adapter


class TestChatCompleteBackwardCompat:
    def test_default_return_is_plain_string(self):
        client, _ = _deepseek_shaped_client("plain content")
        result = chat_complete(client, "sys", "user")
        assert result == "plain content"
        assert isinstance(result, str)

    def test_empty_content_returns_empty_string_not_none(self):
        client, _ = _deepseek_shaped_client(content=None)
        result = chat_complete(client, "sys", "user")
        assert result == ""


class TestChatCompleteReturnUsage:
    def test_return_usage_true_gives_tuple(self):
        client, response = _deepseek_shaped_client("hi there")
        result = chat_complete(client, "sys", "user", return_usage=True)
        assert isinstance(result, tuple)
        content, resp, model = result
        assert content == "hi there"
        assert resp is response
        assert model == "deepseek-chat"

    def test_return_usage_false_still_gives_plain_string(self):
        client, _ = _deepseek_shaped_client("hi there")
        result = chat_complete(client, "sys", "user", return_usage=False)
        assert result == "hi there"
