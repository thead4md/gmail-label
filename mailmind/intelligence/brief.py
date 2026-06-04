"""MailMind — daily brief generation from top-priority items.

Gathers today's reply-needed and high-priority items, then asks the LLM to
synthesize a 3-bullet summary of what needs attention today.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..storage.database import Database
    from ..llm.deepseek import DeepSeekClient

LOG = logging.getLogger(__name__)


def build_daily_brief(
    db: "Database",
    account: Optional[str] = None,
    llm_client: Optional["DeepSeekClient"] = None,
) -> str:
    """Build a 3-bullet daily brief of what needs attention today.

    Gathers today's reply-needed items + top-priority pending items,
    then asks the LLM for a brief summary. Falls back gracefully to ""
    if LLM is unavailable.

    Args:
        db: Database instance for querying items.
        account: Optional email account filter.
        llm_client: Optional DeepSeekClient for summarization (if None, returns "").

    Returns:
        A brief summary string (3 bullets, ~150 chars), or "" if LLM unavailable.
    """
    if llm_client is None:
        LOG.debug("Daily brief: LLM client not available")
        return ""

    try:
        # Get today's items: reply-needed and high-priority (score > 70)
        today_ts = int(datetime.now(timezone.utc).timestamp())
        day_ago_ts = today_ts - 86400

        # Query recent items (created in last 24 hours)
        sql = """
        SELECT p.primary_label, p.priority_score, e.subject, e.sender
        FROM predictions p
        JOIN emails e ON p.email_gmail_id = e.gmail_id
        WHERE p.created_at >= ? AND p.created_at <= ?
        """
        params = [day_ago_ts, today_ts]

        if account:
            sql += " AND p.account = ?"
            params.append(account)

        sql += " ORDER BY p.priority_score DESC LIMIT 20"

        rows = db.execute_sql(sql, params).fetchall()

        if not rows:
            LOG.debug("Daily brief: no items found for today")
            return ""

        # Filter for reply-needed items (we know from reason_json, but here we just
        # take high-priority items as proxy for now)
        items = []
        for row in rows:
            subject = row[2] or "[No Subject]"
            score = row[1] or 0
            label = row[0] or "NOTIFICATION"

            # Include if reply-needed would normally be true, or high priority
            if score > 70:
                items.append(f"{label}: {subject[:60]}")

        if not items:
            LOG.debug("Daily brief: no high-priority items after filtering")
            return ""

        # Build a concise summary prompt
        items_list = "\n".join(f"- {item}" for item in items[:8])
        prompt = f"""Summarize what I need to do today in 3 bullets (max 150 chars total):

{items_list}

Respond with ONLY 3 bullets, no extra text. E.g.:
- Handle urgent finance approval
- Reply to John's project question
- Confirm meeting with Alice"""

        LOG.debug("Calling DeepSeek for daily brief (items=%d)", len(items))

        response = llm_client.client.chat.completions.create(
            model=llm_client.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Create concise daily briefs.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=100,
        )

        content = response.choices[0].message.content
        if not content:
            LOG.warning("Daily brief: DeepSeek returned empty response")
            return ""

        brief = content.strip()[:200]
        LOG.debug("Daily brief generated: %s", brief)
        return brief

    except Exception as e:
        LOG.warning("Failed to generate daily brief: %s", e, exc_info=True)
        return ""
