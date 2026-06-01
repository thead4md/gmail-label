from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mailmind.processing.queue_manager import QueueManager


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def filter_now_items(
    items: List[Dict[str, Any]],
    queue_threshold: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Return items relevant for the Now tab.

    Criteria:
    - reason_json.reply_needed == True, OR
    - priority_score > queue_threshold (stored as int 0-100, threshold as 0.0-1.0)

    Sorted by priority_score DESC, created_at ASC.
    """
    if queue_threshold is None:
        queue_threshold = QueueManager.QUEUE_THRESHOLD

    result = []
    for it in items:
        reason = parse_reason_json(it.get('reason') or it.get('reason_json'))
        keep = False
        if reason.get('reply_needed'):
            keep = True
        score = it.get('priority_score')
        if score is not None and score > int(queue_threshold * 100):
            keep = True
        if keep:
            result.append(it)

    result.sort(key=lambda x: (-(x.get('priority_score') or 0), x.get('created_at') or 0))
    return result


# ---------------------------------------------------------------------------
# Formatting helpers (pure — no Streamlit dependency, fully testable)
# ---------------------------------------------------------------------------

def get_time_ago_str(ts: Optional[int]) -> str:
    """Convert a Unix timestamp to a human-readable relative time string."""
    if not ts:
        return "Never"
    now = int(datetime.now(timezone.utc).timestamp())
    delta = now - ts
    if delta < 60:
        return "< 1 min ago"
    if delta < 3600:
        return f"{delta // 60} min ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def format_unix_ts(ts: Optional[int]) -> str:
    """Format a Unix timestamp as a UTC datetime string, or '—' if absent."""
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_confidence_badge(conf: Optional[float]) -> str:
    """Return a color emoji representing confidence level.

    Green >0.8, amber 0.5–0.8, red <0.5.
    """
    if conf is None:
        return "⚪"
    if conf > 0.8:
        return "🟢"
    if conf > 0.5:
        return "🟡"
    return "🔴"


def parse_reason_json(raw: Any) -> Dict[str, Any]:
    """Safely parse reason_json from a queue item.

    Accepts dict (pass-through), JSON string, or None/invalid (returns {}).
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}
