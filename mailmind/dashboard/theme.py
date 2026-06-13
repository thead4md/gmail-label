"""MailMind Dashboard — CSS design system.

Provides a single `inject_css()` call that writes all custom styles into the
Streamlit page.  Uses CSS custom properties so label colours and trust-tier
colours are defined once and reused everywhere.
"""
from __future__ import annotations

import streamlit as st

# ---------------------------------------------------------------------------
# Semantic colour maps (also used by Python helpers)
# ---------------------------------------------------------------------------

LABEL_COLORS: dict[str, str] = {
    "URGENT":       "#EF4444",
    "WORK":         "#6366F1",
    "FINANCE":      "#22C55E",
    "PERSONAL":     "#F59E0B",
    "NOTIFICATION": "#747D8C",
    "NEWSLETTER":   "#A78BFA",
    "SPAMCANDIDATE":"#FF6B81",
    "DEFER":        "#57606F",
    "CALENDAR":     "#1DBAB4",
    "IMPORTANT":    "#FF6348",
    "MASS_EMAIL":   "#FD79A8",
    "ACTION_REQUIRED": "#FF7F50",
    "MEETING":      "#00CEC9",
    # The user's real (scout-org) taxonomy — hand-picked so they read distinctly.
    "OE":             "#00B894",
    "HIRDETES-L":     "#E17055",
    "INFO-L":         "#4285F4",
    "811/BCS":        "#E84393",
    "811/CSPK LISTA": "#6C5CE7",
    "VÉLEMÉNY-L":     "#F4B400",
}
DEFAULT_LABEL_COLOR = "#6366F1"

# Vivid, well-separated palette used to auto-assign a STABLE colour to any label
# not in LABEL_COLORS (so new taxonomy gets coloured without a code change).
_LABEL_PALETTE: list[str] = [
    "#FF4757", "#6366F1", "#2ED573", "#FFA502", "#A78BFA", "#1DBAB4",
    "#FF6B81", "#4285F4", "#F4B400", "#0F9D58", "#E84393", "#00B894",
    "#E17055", "#6C5CE7", "#FD79A8", "#00CEC9", "#FAB1A0", "#A29BFE",
    "#55EFC4", "#FAB04F", "#74B9FF", "#FF7675",
]


def _hash_label_color(key: str) -> str:
    """Deterministic palette colour for an arbitrary label (stable across runs).

    Uses a fixed FNV-style hash — NOT Python's salted hash() — so the same label
    always maps to the same colour in every process/restart.
    """
    h = 2166136261
    for ch in key:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return _LABEL_PALETTE[h % len(_LABEL_PALETTE)]

CHANNEL_COLORS: dict[str, str] = {
    "newsletter":    "#A78BFA",
    "transactional": "#1DBAB4",
    "team":          "#6366F1",
    "personal":      "#F59E0B",
    "marketing":     "#FF6B81",
    "automated":     "#747D8C",
    "docs":          "#4285F4",
    "calendar":      "#0F9D58",
    "tasks":         "#F4B400",
    "unknown":       "#4A5568",
}

TRUST_COLORS: dict[str, str] = {
    "trusted":   "#22C55E",
    "neutral":   "#F59E0B",
    "watchlist": "#EF4444",
}


def label_color(label: str) -> str:
    """Stable colour for a label. Curated semantic colour when known, otherwise a
    deterministic palette colour hashed from the name (every label gets its own)."""
    key = (label or "").upper()
    if not key:
        return DEFAULT_LABEL_COLOR
    return LABEL_COLORS.get(key) or _hash_label_color(key)


def channel_color(channel: str) -> str:
    return CHANNEL_COLORS.get((channel or "").lower(), CHANNEL_COLORS["unknown"])


def trust_color(tier: str) -> str:
    return TRUST_COLORS.get((tier or "").lower(), TRUST_COLORS["neutral"])


# ---------------------------------------------------------------------------
# CSS injection
# ---------------------------------------------------------------------------

_CSS = """
/* Inter is loaded non-blocking via <link rel=preconnect>+display=swap in
   inject_css(); the --mm-font stack below renders a system font instantly
   until Inter arrives. (The old CSS @import here was render-blocking and
   serialised first paint on cold load.) */

/* ─── CSS custom properties ─────────────────────────────────────── */
:root {
  --mm-font:        'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  --mm-bg:          #0B0D12;
  --mm-surface:     #13161D;
  --mm-surface-2:   #1A1E27;
  --mm-surface-3:   #222632;
  --mm-border:      #2A2F3C;
  --mm-border-soft: #1E222C;
  --mm-primary:     #6366F1;
  --mm-secondary:   #A78BFA;
  --mm-success:     #22C55E;
  --mm-warning:     #F59E0B;
  --mm-danger:      #EF4444;
  --mm-text:        #E8EAF0;
  --mm-text-muted:  #9AA3B4;
  --mm-text-faint:  #5A6273;
  --mm-radius:      10px;
  --mm-radius-sm:   6px;
  --mm-shadow:      0 4px 24px rgba(0,0,0,.45);
  --mm-shadow-sm:   0 2px 8px rgba(0,0,0,.30);
  font-family: var(--mm-font);
}

/* ─── Base overrides ────────────────────────────────────────────── */
.stApp, [data-testid="stAppViewContainer"] {
  background: var(--mm-bg) !important;
  font-family: var(--mm-font) !important;
}

/* hide default Streamlit top-bar decoration */
[data-testid="stDecoration"] { display: none !important; }
#MainMenu, footer { visibility: hidden !important; }
[data-testid="stHeader"] { background: transparent !important; height: 0 !important; }

/* ─── Sidebar ───────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
  background: var(--mm-surface) !important;
  border-right: 1px solid var(--mm-border) !important;
}
[data-testid="stSidebar"] * { font-family: var(--mm-font) !important; }
[data-testid="stSidebarContent"] { padding: 1.25rem 1rem !important; }

/* ─── Tabs ──────────────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
  background: var(--mm-surface) !important;
  border-radius: var(--mm-radius) var(--mm-radius) 0 0 !important;
  border-bottom: 1px solid var(--mm-border) !important;
  gap: 4px !important;
  padding: 0 8px !important;
}
.stTabs [data-baseweb="tab"] {
  background: transparent !important;
  border-radius: var(--mm-radius-sm) var(--mm-radius-sm) 0 0 !important;
  color: var(--mm-text-muted) !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  padding: 10px 18px !important;
  border-bottom: 2px solid transparent !important;
  transition: color .2s, border-color .2s !important;
}
.stTabs [aria-selected="true"] {
  color: var(--mm-primary) !important;
  border-bottom: 2px solid var(--mm-primary) !important;
  background: transparent !important;
}
.stTabs [data-baseweb="tab"]:hover:not([aria-selected="true"]) {
  color: var(--mm-text) !important;
}
.stTabs [data-baseweb="tab-panel"] {
  background: var(--mm-bg) !important;
  padding: 20px 0 !important;
}

/* ─── Metric cards ──────────────────────────────────────────────── */
[data-testid="metric-container"] {
  background: var(--mm-surface) !important;
  border: 1px solid var(--mm-border) !important;
  border-radius: var(--mm-radius) !important;
  padding: 16px 20px !important;
  box-shadow: var(--mm-shadow-sm) !important;
}
[data-testid="metric-container"] label {
  color: var(--mm-text-muted) !important;
  font-size: 11px !important;
  font-weight: 600 !important;
  letter-spacing: .08em !important;
  text-transform: uppercase !important;
}
[data-testid="metric-container"] [data-testid="stMetricValue"] {
  color: var(--mm-text) !important;
  font-size: 26px !important;
  font-weight: 700 !important;
  line-height: 1.2 !important;
}
[data-testid="metric-container"] [data-testid="stMetricDelta"] {
  font-size: 12px !important;
}

/* ─── Buttons ───────────────────────────────────────────────────── */
.stButton > button {
  background: var(--mm-surface-2) !important;
  border: 1px solid var(--mm-border) !important;
  border-radius: var(--mm-radius-sm) !important;
  color: var(--mm-text) !important;
  font-family: var(--mm-font) !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  transition: all .15s !important;
  padding: 6px 14px !important;
}
.stButton > button:hover {
  background: var(--mm-surface-3) !important;
  border-color: var(--mm-primary) !important;
  color: var(--mm-primary) !important;
  transform: translateY(-1px) !important;
}
.stButton > button:active {
  transform: translateY(0) !important;
}

/* ─── Primary action button (approve) ──────────────────────────── */
.mm-btn-approve > button {
  background: rgba(46,213,115,.12) !important;
  border-color: var(--mm-success) !important;
  color: var(--mm-success) !important;
}
.mm-btn-approve > button:hover {
  background: rgba(46,213,115,.22) !important;
  border-color: var(--mm-success) !important;
  color: var(--mm-success) !important;
}
.mm-btn-reject > button {
  background: rgba(255,71,87,.12) !important;
  border-color: var(--mm-danger) !important;
  color: var(--mm-danger) !important;
}
.mm-btn-reject > button:hover {
  background: rgba(255,71,87,.22) !important;
}

/* ─── Expanders ─────────────────────────────────────────────────── */
[data-testid="stExpander"] {
  background: var(--mm-surface) !important;
  border: 1px solid var(--mm-border) !important;
  border-radius: var(--mm-radius) !important;
  margin-bottom: 8px !important;
  box-shadow: var(--mm-shadow-sm) !important;
  transition: border-color .2s !important;
}
[data-testid="stExpander"]:hover {
  border-color: var(--mm-primary) !important;
}
[data-testid="stExpander"] summary {
  font-size: 13px !important;
  font-weight: 500 !important;
  color: var(--mm-text) !important;
  padding: 12px 16px !important;
}

/* ─── Containers ────────────────────────────────────────────────── */
[data-testid="stVerticalBlock"] > [data-testid="element-container"]
  > [data-testid="stVerticalBlock"][class*="stBorderContainer"] {
  background: var(--mm-surface) !important;
  border: 1px solid var(--mm-border) !important;
  border-radius: var(--mm-radius) !important;
  box-shadow: var(--mm-shadow-sm) !important;
}

/* ─── Dataframe ─────────────────────────────────────────────────── */
[data-testid="stDataFrameContainer"] {
  border: 1px solid var(--mm-border) !important;
  border-radius: var(--mm-radius) !important;
  overflow: hidden !important;
  background: var(--mm-surface) !important;
}

/* ─── Selectbox / toggles ───────────────────────────────────────── */
[data-testid="stSelectbox"] > div > div {
  background: var(--mm-surface-2) !important;
  border-color: var(--mm-border) !important;
  border-radius: var(--mm-radius-sm) !important;
  color: var(--mm-text) !important;
}
/* Selectbox dropdown popover (fixes white list on dark background) */
[data-baseweb="popover"],
[data-baseweb="popover"] > div {
  background: var(--mm-surface-2) !important;
}
[data-baseweb="popover"] ul,
[data-baseweb="popover"] [role="listbox"] {
  background: var(--mm-surface-2) !important;
  border: 1px solid var(--mm-border) !important;
}
[data-baseweb="popover"] li,
[data-baseweb="popover"] [role="option"] {
  background: var(--mm-surface-2) !important;
  color: var(--mm-text) !important;
}
[data-baseweb="popover"] li:hover,
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="popover"] [aria-selected="true"],
[data-baseweb="popover"] [aria-current="true"] {
  background: var(--mm-surface-3) !important;
  color: var(--mm-text) !important;
}

/* ─── Dividers ──────────────────────────────────────────────────── */
hr { border-color: var(--mm-border) !important; margin: 20px 0 !important; }

/* ─── Text ──────────────────────────────────────────────────────── */
h1,h2,h3,h4 { color: var(--mm-text) !important; font-family: var(--mm-font) !important; }
p, li, span { color: var(--mm-text) !important; }
.stMarkdown p { font-size: 13px !important; }
caption, .stCaption { color: var(--mm-text-muted) !important; font-size: 11px !important; }

/* ─── KPI cards (NOW tab overview row) ──────────────────────────── */
.mm-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 18px;
}
.mm-kpi-card {
  background: linear-gradient(140deg, var(--mm-surface) 0%, var(--mm-surface-2) 100%);
  border: 1px solid var(--mm-border);
  border-left: 3px solid var(--mm-primary);
  border-radius: var(--mm-radius);
  padding: 14px 16px;
  box-shadow: var(--mm-shadow-sm);
  display: flex; flex-direction: column; gap: 6px;
  min-width: 0;
}
.mm-kpi-top { display: flex; align-items: center; gap: 8px; }
.mm-kpi-icon { font-size: 16px; line-height: 1; }
.mm-kpi-label {
  font-size: 10px; font-weight: 700;
  letter-spacing: .08em; text-transform: uppercase;
  color: var(--mm-text-muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.mm-kpi-value {
  font-size: 28px; font-weight: 700; line-height: 1.1;
  color: var(--mm-text);
}
.mm-kpi-delta { font-size: 11px; font-weight: 600; }
.mm-kpi-delta-up   { color: var(--mm-success); }
.mm-kpi-delta-down { color: var(--mm-danger); }
.mm-kpi-delta-flat { color: var(--mm-text-faint); }

/* ─── Custom MailMind card components ───────────────────────────── */
.mm-card {
  background: var(--mm-surface);
  border: 1px solid var(--mm-border);
  border-radius: var(--mm-radius);
  border-left-width: 3px;
  padding: 14px 16px;
  margin-bottom: 8px;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  transition: background .15s, box-shadow .15s;
  box-shadow: var(--mm-shadow-sm);
}
.mm-card:hover {
  background: var(--mm-surface-2);
  box-shadow: var(--mm-shadow);
}
.mm-avatar {
  width: 36px; height: 36px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; font-weight: 700;
  flex-shrink: 0;
  text-transform: uppercase;
}
.mm-card-body { flex: 1; min-width: 0; }
.mm-sender {
  font-size: 12px; font-weight: 600;
  color: var(--mm-text-muted);
  margin-bottom: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.mm-subject {
  font-size: 14px; font-weight: 600;
  color: var(--mm-text);
  margin-bottom: 4px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.mm-snippet {
  font-size: 12px; color: var(--mm-text-muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.mm-meta {
  display: flex; align-items: center; gap: 8px;
  margin-top: 6px; flex-wrap: wrap;
}
.mm-chip {
  display: inline-flex; align-items: center;
  padding: 2px 8px; border-radius: 20px;
  font-size: 10px; font-weight: 700;
  letter-spacing: .06em; text-transform: uppercase;
  border: 1px solid;
}
.mm-conf-bar-wrap {
  width: 80px; height: 4px;
  background: var(--mm-border);
  border-radius: 2px; overflow: hidden;
}
.mm-conf-bar { height: 100%; border-radius: 2px; }
.mm-pill-reply {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 8px; border-radius: 20px;
  background: rgba(99,102,241,.15);
  border: 1px solid rgba(99,102,241,.4);
  color: var(--mm-primary); font-size: 10px; font-weight: 700;
}
.mm-time {
  font-size: 11px; color: var(--mm-text-faint);
}
.mm-trust-badge {
  display: inline-block;
  padding: 2px 8px; border-radius: 20px;
  font-size: 10px; font-weight: 700;
  letter-spacing: .04em;
}

/* ─── Section headers ───────────────────────────────────────────── */
.mm-section-header {
  font-size: 11px; font-weight: 700;
  letter-spacing: .12em; text-transform: uppercase;
  color: var(--mm-text-muted);
  margin: 24px 0 12px 0;
  display: flex; align-items: center; gap: 8px;
}
.mm-section-header::after {
  content: '';
  flex: 1; height: 1px;
  background: var(--mm-border);
}

/* ─── Why this panel ────────────────────────────────────────────── */
.mm-reason-panel {
  background: var(--mm-surface-2);
  border: 1px solid var(--mm-border-soft);
  border-radius: var(--mm-radius-sm);
  padding: 12px 14px;
  margin: 8px 0;
  font-size: 12px;
}
.mm-reason-row {
  display: flex; align-items: baseline;
  gap: 8px; padding: 3px 0;
  border-bottom: 1px solid var(--mm-border-soft);
}
.mm-reason-row:last-child { border-bottom: none; }
.mm-reason-key {
  font-size: 10px; font-weight: 700;
  text-transform: uppercase; letter-spacing: .08em;
  color: var(--mm-text-muted); min-width: 120px;
}
.mm-reason-val { color: var(--mm-text); }

/* ─── Status indicators ─────────────────────────────────────────── */
.mm-status-dot {
  display: inline-block;
  width: 8px; height: 8px; border-radius: 50%;
  margin-right: 6px; vertical-align: middle;
}
.mm-status-fresh  {
  background: var(--mm-success);
  box-shadow: 0 0 6px var(--mm-success);
  animation: mm-pulse 2s ease-in-out infinite;
}
@keyframes mm-pulse {
  0%,100% { box-shadow: 0 0 4px var(--mm-success); opacity: 1; }
  50%     { box-shadow: 0 0 10px var(--mm-success); opacity: .65; }
}
.mm-status-stale  { background: var(--mm-danger);  box-shadow: 0 0 6px var(--mm-danger);  }
.mm-status-never  { background: var(--mm-warning);                                         }

/* ─── Empty state ───────────────────────────────────────────────── */
.mm-empty {
  text-align: center; padding: 48px 24px;
  color: var(--mm-text-muted);
}
.mm-empty-icon { font-size: 40px; margin-bottom: 12px; }
.mm-empty-text { font-size: 14px; font-weight: 500; }
.mm-empty-sub  { font-size: 12px; color: var(--mm-text-faint); margin-top: 4px; }

/* ─── Email snippet preview box ────────────────────────────────── */
.mm-preview-box {
  background: var(--mm-surface-2);
  border: 1px solid var(--mm-border-soft);
  border-left: 3px solid var(--mm-primary);
  border-radius: 0 var(--mm-radius-sm) var(--mm-radius-sm) 0;
  padding: 10px 14px;
  margin: -4px 0 8px 48px;
  font-size: 12px;
  color: var(--mm-text-muted);
  line-height: 1.6;
  font-style: italic;
}
/* ─── Styled HTML table (replaces st.dataframe) ────────────────── */
.mm-table-wrap {
  background: var(--mm-surface);
  border: 1px solid var(--mm-border);
  border-radius: var(--mm-radius);
  overflow: hidden;
  overflow-x: auto;
  box-shadow: var(--mm-shadow-sm);
  margin-bottom: 12px;
}
.mm-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 12px;
  font-family: var(--mm-font);
}
.mm-table thead tr { background: var(--mm-surface-2); }
.mm-table thead th {
  padding: 8px 12px;
  text-align: left;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .10em;
  text-transform: uppercase;
  color: var(--mm-text-muted);
  border-bottom: 1px solid var(--mm-border);
  white-space: nowrap;
}
.mm-table tbody tr {
  border-bottom: 1px solid var(--mm-border-soft);
  transition: background .12s;
}
.mm-table tbody tr:hover { background: var(--mm-surface-2); }
.mm-table tbody td { padding: 8px 12px; color: var(--mm-text); vertical-align: middle; }
.mm-table tbody tr:last-child { border-bottom: none; }

/* ─── Alert tweaks ──────────────────────────────────────────────── */
[data-testid="stAlert"] {
  border-radius: var(--mm-radius-sm) !important;
  font-size: 13px !important;
}

/* ─── Mobile (≤768px) ───────────────────────────────────────────── */
@media (max-width: 768px) {
  [data-testid="column"] {
    width: 100% !important;
    flex: 1 1 100% !important;
    min-width: 100% !important;
  }
  .mm-kpi-grid { grid-template-columns: repeat(2, 1fr); gap: 8px; }
  .mm-kpi-value { font-size: 22px; }
  .mm-card { flex-direction: row; padding: 12px; }
  .mm-subject { white-space: normal; }
  .stButton > button { width: 100% !important; padding: 10px 14px !important; }
  .stTabs [data-baseweb="tab"] { padding: 8px 10px !important; font-size: 12px !important; }
  [data-testid="stMetricValue"] { font-size: 20px !important; }
  [data-testid="stDataFrameContainer"] { overflow-x: auto !important; }
}
"""


_LIGHT_VARS = """
:root {
  --mm-bg:          #F4F5F8;
  --mm-surface:     #FFFFFF;
  --mm-surface-2:   #F7F8FB;
  --mm-surface-3:   #EEF0F5;
  --mm-border:      #D6DAE3;
  --mm-border-soft: #E5E8EF;
  --mm-text:        #161A22;
  --mm-text-muted:  #565E6E;
  --mm-text-faint:  #939BAB;
  --mm-shadow:      0 4px 24px rgba(0,0,0,.10);
  --mm-shadow-sm:   0 2px 8px rgba(0,0,0,.06);
}
"""

_SYSTEM_VARS = f"@media (prefers-color-scheme: light) {{ {_LIGHT_VARS} }}"


# Non-blocking Inter load: preconnect warms the TLS handshake, and the
# stylesheet <link> (display=swap baked into the URL) fetches async without
# serialising first paint — unlike the old render-blocking CSS @import. The
# --mm-font stack paints a system font until Inter is ready.
_FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" '
    'href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap">'
)


def inject_css(theme: str = "dark") -> None:
    """Inject the full MailMind CSS theme. theme: 'dark' | 'light' | 'system'."""
    st.markdown(_FONT_LINKS, unsafe_allow_html=True)
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)
    if theme == "light":
        st.markdown(f"<style>{_LIGHT_VARS}</style>", unsafe_allow_html=True)
    elif theme == "system":
        st.markdown(f"<style>{_SYSTEM_VARS}</style>", unsafe_allow_html=True)
