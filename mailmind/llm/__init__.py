"""LLM module for MailMind Pass 7+.

Provides unified LLM-based email classification through a Protocol interface.
Supports multiple providers (DeepSeek, OpenAI, etc.) behind a common interface.

Modules:
- base.py: LLMClassifier Protocol and LLMResult dataclass
- deepseek.py: DeepSeek API client implementation
- llm_classifier.py: OpenAI API client implementation

All LLM calls are external API calls and must be mocked in tests.
"""

__all__ = ["deepseek", "base"]

from mailmind.llm.base import LLMClassifier, LLMResult
from mailmind.llm.deepseek import DeepSeekClient
