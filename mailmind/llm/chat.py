"""Provider-agnostic chat completion helper.

MailMind has two LLM client shapes that must be callable interchangeably:

  * ``DeepSeekClient`` exposes ``.client`` (an OpenAI-compatible client) + ``.model``.
  * ``OpenAIAdapter`` wraps an ``LLMClassifier`` (``.classifier.api_key`` / ``.model``)
    and constructs its OpenAI client on demand — it has NO persistent ``.client``.

Call sites that reached into ``llm_client.client.chat.completions`` directly worked
for DeepSeek but raised ``AttributeError`` under ``LLM_PROVIDER=openai``. Route every
free-form chat completion (daily brief, NL-rule parsing, label discovery) through
``chat_complete`` so they are provider-agnostic.
"""
from __future__ import annotations

from typing import Any


def chat_complete(
    llm_client: Any,
    system: str,
    user: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 60,
    json_mode: bool = False,
) -> str:
    """Run one chat completion against whichever client shape was passed.

    Returns the assistant message content ("" when the model returns nothing).
    Raises RuntimeError if the client exposes neither supported shape.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    kwargs: dict = {"temperature": temperature, "max_tokens": max_tokens}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    # Shape 1: DeepSeekClient-style — a persistent OpenAI-compatible client + model.
    client = getattr(llm_client, "client", None)
    model = getattr(llm_client, "model", None)
    if client is not None and model:
        resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return resp.choices[0].message.content or ""

    # Shape 2: OpenAIAdapter — build the OpenAI client on demand from the wrapped
    # classifier's credentials.
    inner = getattr(llm_client, "classifier", None)
    if inner is not None and getattr(inner, "api_key", None):
        import openai
        oc = openai.OpenAI(api_key=inner.api_key)
        resp = oc.chat.completions.create(
            model=getattr(inner, "model", "gpt-4o-mini"),
            messages=messages,
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    raise RuntimeError("no usable LLM chat interface on client")
