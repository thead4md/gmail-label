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


def get_heartbeat_status(
    last_heartbeat_ts: Optional[int],
    *,
    expected_interval_seconds: int = 120,
    stale_after_intervals: int = 3,
) -> Dict[str, Any]:
    """Read the watch loop's heartbeat and classify its freshness.

    Returns a dict with:
      status: 'never' | 'fresh' | 'stale'
      seconds_ago: int | None
      human: str — short label for the UI
    Stale = no heartbeat for more than ``stale_after_intervals * expected_interval_seconds``
    (default: 3 missed cycles at the default 120s poll = ~6 minutes silent).
    """
    if last_heartbeat_ts is None:
        return {"status": "never", "seconds_ago": None,
                "human": "no heartbeat yet"}
    now = int(datetime.now(timezone.utc).timestamp())
    age = max(0, now - int(last_heartbeat_ts))
    threshold = expected_interval_seconds * stale_after_intervals
    if age > threshold:
        return {"status": "stale", "seconds_ago": age,
                "human": f"silent for {get_time_ago_str(last_heartbeat_ts)}"}
    return {"status": "fresh", "seconds_ago": age,
            "human": f"active {get_time_ago_str(last_heartbeat_ts)}"}


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


# ---------------------------------------------------------------------------
# Rich HTML visual helpers (returned as strings, rendered via st.markdown)
# These do NOT import streamlit — fully testable.
# ---------------------------------------------------------------------------

def sender_avatar_html(sender: Optional[str], color: str = "#5B8AF0") -> str:
    """Return an HTML circle avatar with the sender's first initial."""
    initial = "?"
    s = (sender or "").strip()
    # Try display name part before <addr>
    if "<" in s:
        s = s.split("<")[0].strip()
    if s:
        initial = s[0].upper()
    # Derive a stable background from the initial
    hues = {"A":220,"B":170,"C":280,"D":30,"E":190,"F":340,"G":120,"H":200,
            "I":260,"J":40,"K":155,"L":310,"M":20,"N":230,"O":50,"P":270,
            "Q":80,"R":0,"S":140,"T":200,"U":310,"V":60,"W":180,"X":300,
            "Y":90,"Z":230}
    hue = hues.get(initial, 220)
    bg  = f"hsl({hue},60%,38%)"
    return (
        f'<div class="mm-avatar" style="background:{bg};color:#fff;">'
        f'{initial}</div>'
    )


def label_chip_html(label: Optional[str]) -> str:
    """Render a coloured label chip."""
    from mailmind.dashboard.theme import label_color
    lbl = (label or "").upper()
    color = label_color(lbl)
    return (
        f'<span class="mm-chip" '
        f'style="color:{color};border-color:{color}20;background:{color}18;">'
        f'{lbl}</span>'
    )


def channel_chip_html(channel: Optional[str]) -> str:
    """Render a channel chip (newsletter, team, transactional, …)."""
    from mailmind.dashboard.theme import channel_color
    ch    = (channel or "unknown").lower()
    color = channel_color(ch)
    icon  = {
        "newsletter":    "📨",
        "transactional": "🧾",
        "team":          "👥",
        "personal":      "💬",
        "marketing":     "📣",
        "automated":     "🤖",
    }.get(ch, "📧")
    return (
        f'<span class="mm-chip" '
        f'style="color:{color};border-color:{color}20;background:{color}18;">'
        f'{icon} {ch}</span>'
    )


def confidence_bar_html(conf: Optional[float]) -> str:
    """Return an inline HTML confidence bar (green/amber/red)."""
    v = conf or 0.0
    pct = int(v * 100)
    color = "#2ED573" if v > 0.8 else "#FFA502" if v > 0.5 else "#FF4757"
    return (
        f'<div style="display:inline-flex;align-items:center;gap:6px;">'
        f'<div class="mm-conf-bar-wrap">'
        f'<div class="mm-conf-bar" style="width:{pct}%;background:{color};"></div>'
        f'</div>'
        f'<span style="font-size:11px;color:{color};font-weight:600;">{pct}%</span>'
        f'</div>'
    )


def trust_badge_html(tier: Optional[str]) -> str:
    """Return a coloured trust-tier badge."""
    from mailmind.dashboard.theme import trust_color
    t     = (tier or "neutral").lower()
    color = trust_color(t)
    icon  = {"trusted": "✅", "neutral": "⚪", "watchlist": "🚫"}.get(t, "⚪")
    return (
        f'<span class="mm-trust-badge" '
        f'style="background:{color}20;color:{color};border:1px solid {color}40;">'
        f'{icon} {t}</span>'
    )


def reply_needed_pill_html() -> str:
    """Return a 'Reply Needed' pill badge."""
    return '<span class="mm-pill-reply">💬 Reply needed</span>'


def email_card_html(
    subject: str,
    sender: str,
    time_ago: str,
    label: Optional[str] = None,
    channel: Optional[str] = None,
    confidence: Optional[float] = None,
    reply_needed: bool = False,
    thread_summary: Optional[str] = None,
) -> str:
    """Compose the full HTML for a NOW-tab email card (display only).

    Interactive widgets (Approve button) must be rendered separately
    via st.button() after this markdown block.
    """
    from mailmind.dashboard.theme import label_color

    lbl_color = label_color((label or "").upper())
    avatar    = sender_avatar_html(sender)
    chips     = ""
    if label:
        chips += label_chip_html(label) + " "
    if channel and channel != "unknown":
        chips += channel_chip_html(channel) + " "
    conf_bar = confidence_bar_html(confidence) if confidence is not None else ""
    reply_p  = reply_needed_pill_html() if reply_needed else ""
    summary_row = ""
    if thread_summary:
        trunc = thread_summary[:120] + ("…" if len(thread_summary) > 120 else "")
        summary_row = (
            f'<div class="mm-snippet" style="margin-top:4px;font-style:italic;">'
            f'"{trunc}"</div>'
        )

    sender_short = (sender or "Unknown").split("<")[0].strip()[:40]
    subj_short   = (subject or "[No Subject]")[:70]

    return f"""
<div class="mm-card" style="border-left-color:{lbl_color};">
  {avatar}
  <div class="mm-card-body">
    <div class="mm-sender">{sender_short}</div>
    <div class="mm-subject">{subj_short}</div>
    {summary_row}
    <div class="mm-meta">
      {chips}
      {conf_bar}
      {reply_p}
      <span class="mm-time">{time_ago}</span>
    </div>
  </div>
</div>
"""


def action_items_html(items: Optional[list]) -> str:
    """Render a 📋 chip listing action items; empty string if none."""
    items = items or []
    if not items:
        return ""
    lines = "".join(
        f'<div style="font-size:12px;color:#E2E8F0;padding:2px 0;">• {i}</div>'
        for i in items[:5]
    )
    return (
        f'<details style="margin-top:4px;">'
        f'<summary style="font-size:11px;color:#5B8AF0;cursor:pointer;">'
        f'📋 {len(items)} action item(s)</summary>{lines}</details>'
    )


def deadline_pill_html(deadlines: Optional[list]) -> str:
    """Render a ⏰ red deadline pill; empty string if none."""
    deadlines = deadlines or []
    if not deadlines:
        return ""
    first = deadlines[0][:60]
    return (
        f'<span class="mm-chip" style="color:#FF4757;border-color:#FF475740;'
        f'background:#FF475718;">⏰ {first}</span>'
    )


def confidence_sparkline_html(reason: Optional[dict]) -> str:
    """Inline SVG sparkline of rules→ML→LLM confidence votes.
    Reads ml_confidence / llm_confidence / score (0-100) from reason_json.
    Returns '' if there is nothing to plot.
    """
    reason = reason or {}
    pts = []
    score = reason.get("score")
    if isinstance(score, (int, float)):
        pts.append(("rules", max(0.0, min(1.0, score / 100.0))))
    if reason.get("ml_confidence") is not None:
        pts.append(("ml", float(reason["ml_confidence"])))
    if reason.get("llm_confidence") is not None:
        pts.append(("llm", float(reason["llm_confidence"])))
    if len(pts) < 2:
        return ""
    w, h, n = 120, 28, len(pts)
    step = w / (n - 1)
    coords = [(i * step, h - (v * h)) for i, (_, v) in enumerate(pts)]
    path = " ".join(
        ("M" if i == 0 else "L") + f"{x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords)
    )
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="#5B8AF0"/>'
        for x, y in coords
    )
    return (
        f'<svg width="{w}" height="{h}" style="vertical-align:middle;">'
        f'<path d="{path}" stroke="#5B8AF0" stroke-width="2" fill="none"/>'
        f'{dots}</svg>'
    )


def email_preview_html(snippet: Optional[str]) -> str:
    """Render a snippet preview box beneath an email card. Returns '' if empty."""
    if not snippet:
        return ""
    safe = snippet.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")[:400]
    return f'<div class="mm-preview-box">{safe}</div>'


def sender_table_html(profiles: List[Dict[str, Any]]) -> str:
    """Styled HTML table for sender profiles (replaces st.dataframe)."""
    if not profiles:
        return ""
    rows_html = ""
    for p in profiles:
        email = p.get("sender_email") or "—"
        tier  = p.get("trust_tier") or "neutral"
        seen  = p.get("total_seen", 0)
        rate  = p.get("approval_rate", 0.0)
        rows_html += (
            f"<tr>"
            f'<td style="width:44px;padding:6px 8px;">{sender_avatar_html(email)}</td>'
            f"<td>{email}</td>"
            f"<td>{trust_badge_html(tier)}</td>"
            f'<td style="text-align:right;color:var(--mm-text-muted);">{seen}</td>'
            f"<td>{confidence_bar_html(rate)}</td>"
            f"</tr>"
        )
    return (
        '<div class="mm-table-wrap"><table class="mm-table">'
        '<thead><tr><th style="width:44px;"></th><th>Sender</th><th>Trust</th>'
        '<th style="text-align:right;">Seen</th><th>Approval rate</th></tr></thead>'
        f"<tbody>{rows_html}</tbody></table></div>"
    )
