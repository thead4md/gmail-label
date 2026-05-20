"""LLM module for MailMind Pass 7+.

Provides DeepSeek-based email classification as an optional stage
in the processing pipeline. All LLM calls are external API calls
and must be mocked in tests.

This module contains:
- deepseek.py: DeepSeek API client implementation

Future extensions: support for other LLM providers (OpenAI, Claude, etc.)
can be added as additional modules in this package.
"""

__all__ = ["deepseek"]

from mailmind.llm.deepseek import DeepSeekClient, LLMResult
