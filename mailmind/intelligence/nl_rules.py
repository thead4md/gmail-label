"""Parse natural language sentences into sender->label rules.

Uses DeepSeek to extract structured rule components from user input.
Validates labels against the taxonomy and rejects unsupported clause types.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from ..llm.deepseek import DeepSeekClient
from ..config import MailMindConfig
from ..taxonomy import ALL_LABELS

LOG = logging.getLogger(__name__)


def parse_rule_nl(text: str, client: DeepSeekClient) -> dict:
    """Parse a natural language rule sentence into structured rule components.

    Args:
        text: User input, e.g. "label anything from billing@acme.com as FINANCE"
              or "label emails from oe-l@cserkesz.hu about events as CALENDAR"
        client: DeepSeekClient instance for LLM parsing

    Returns:
        dict with keys:
        - sender_email: str|None (extracted email)
        - label: str|None (extracted label name, uppercase)
        - match_pattern: str|None (subject regex when the rule is topic-scoped;
          None for a catch-all rule — only present on the success path)
        - unsupported: bool (True if clause type not yet storable)
        - error: str|None (error message if parsing failed)
    """
    if not text or not text.strip():
        return {
            "sender_email": None,
            "label": None,
            "unsupported": False,
            "error": "Please enter a rule description.",
        }

    try:
        # Call DeepSeek to extract rule components
        system_prompt = (
            "You are a rule parser. Extract sender email and label from natural language.\n"
            "Return JSON with: sender_email (str or null), label (str or null), "
            "match_pattern (str or null), unsupported (bool), unsupported_reason (str or null).\n"
            "match_pattern: when the rule is scoped to a TOPIC or subject condition "
            "(e.g. 'emails about events', 'messages with invoice', 'meeting invites'), "
            "return a case-insensitive regex alternation of the relevant keywords "
            "(e.g. 'invoice|receipt|payment'); otherwise null for a catch-all rule "
            "that applies to every message from the sender. Include the user's language "
            "synonyms if they wrote in another language.\n"
            "unsupported=true if the sentence describes actions we can't store yet "
            "(e.g., 'never archive', 'auto-delete', 'priority inbox'). "
            "These clauses are unsupported, but don't fail parsing."
        )

        user_prompt = f"Parse this rule: {text}"

        response = client.client.chat.completions.create(
            model=client.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=150,
        )

        content = response.choices[0].message.content
        if not content:
            return {
                "sender_email": None,
                "label": None,
                "unsupported": False,
                "error": "LLM returned empty response.",
            }

        parsed = json.loads(content)
        sender_email = parsed.get("sender_email")
        label = parsed.get("label")
        match_pattern = parsed.get("match_pattern") or None
        if isinstance(match_pattern, str):
            match_pattern = match_pattern.strip() or None
        unsupported = parsed.get("unsupported", False)
        unsupported_reason = parsed.get("unsupported_reason")

        # Validate label if present
        if label:
            label = label.strip().upper()
            if label not in ALL_LABELS:
                return {
                    "sender_email": sender_email,
                    "label": None,
                    "unsupported": False,
                    "error": f"Unknown label '{label}'. Valid labels: {', '.join(sorted(ALL_LABELS))}",
                }

        # Report unsupported clauses
        if unsupported:
            reason_text = unsupported_reason or "unsupported clause"
            return {
                "sender_email": sender_email,
                "label": label,
                "unsupported": True,
                "error": f"This rule contains an unsupported action: {reason_text}. "
                         f"Currently only sender->label rules are supported.",
            }

        # Validate sender_email and label are present
        if not sender_email:
            return {
                "sender_email": None,
                "label": label,
                "unsupported": False,
                "error": "Could not extract sender email from your description.",
            }

        if not label:
            return {
                "sender_email": sender_email,
                "label": None,
                "unsupported": False,
                "error": "Could not extract a label from your description.",
            }

        # Success
        return {
            "sender_email": sender_email,
            "label": label,
            "match_pattern": match_pattern,
            "unsupported": False,
            "error": None,
        }

    except json.JSONDecodeError:
        return {
            "sender_email": None,
            "label": None,
            "unsupported": False,
            "error": "Failed to parse LLM response (invalid JSON).",
        }
    except Exception as e:
        LOG.error("Error parsing NL rule: %s", e, exc_info=True)
        return {
            "sender_email": None,
            "label": None,
            "unsupported": False,
            "error": f"Error parsing rule: {str(e)}",
        }
