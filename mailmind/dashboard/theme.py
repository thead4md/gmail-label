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
    "URGENT":       "#FF4757",
    "WORK":         "#5B8AF0",
    "FINANCE":      "#2ED573",
    "PERSONAL":     "#FFA502",
    "NOTIFICATION": "#747D8C",
    "NEWSLETTER":   "#9B6DFF",
    "SPAMCANDIDATE":"#FF6B81",
    "DEFER":        "#57606F",
    "CALENDAR":     "#1DBAB4",
    "IMPORTANT":    "#FF6348",
}
DEFAULT_LABEL_COLOR = "#5B8AF0"

CHANNEL_COLORS: dict[str, str] = {
    "newsletter":    "#9B6DFF",
    "transactional": "#1DBAB4",
    "team":          "#5B8AF0",
    "personal":      "#FFA502",
    "marketing":     "#FF6B81",
    "automated":     "#747D8C",
    "unknown":       "#4A5568",
}

TRUST_COLORS: dict[str, str] = {
    "trusted":   "#2ED573",
    "neutral":   "#FFA502",
    "watchlist": "#FF4757",
}


def label_color(label: str) -> str:
    return LABEL_COLORS.get((label or "").upper(), DEFAULT_LABEL_COLOR)


def channel_color(channel: str) -> str:
    return CHANNEL_COLORS.get((channel or "").lower(), CHANNEL_COLORS["unknown"])


def trust_color(tier: str) -> str:
    return TRUST_COLORS.get((tier or "").lower(), TRUST_COLORS["neutral"])


# ---------------------------------------------------------------------------
# CSS injection
# ---------------------------------------------------------------------------

_CSS = """
/* ─── Google Inter font ─────────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* ─── CSS custom properties ─────────────────────────────────────── */
:root {
  --mm-bg:          #0A0E1A;
  --mm-surface:     #141928;
  --mm-surface-2:   #1C2237;
  --mm-surface-3:   #232B42;
  --mm-border:      #2D3656;
  --mm-border-soft: #1E2740;
  --mm-primary:     #5B8AF0;
  --mm-secondary:   #9B6DFF;
  --mm-success:     #2ED573;
  --mm-warning:     #FFA502;
  --mm-danger:      #FF4757;
  --mm-text:        #E2E8F0;
  --mm-text-muted:  #94A3B8;
  --mm-text-faint:  #4A5568;
  --mm-radius:      10px;
  --mm-radius-sm:   6px;
  --mm-shadow:      0 4px 24px rgba(0,0,0,.45);
  --mm-shadow-sm:   0 2px 8px rgba(0,0,0,.30);
  font-family: 'Inter', sans-serif;
}

/* ─── Base overrides ────────────────────────────────────────────── */
.stApp, [data-testid="stAppViewContainer"] {
  background: var(--mm-bg) !important;
  font-family: 'Inter', sans-serif !important;
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
[data-testid="stSidebar"] * { font-family: 'Inter', sans-serif !important; }
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
  font-family: 'Inter', sans-serif !important;
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

/* ─── Dividers ──────────────────────────────────────────────────── */
hr { border-color: var(--mm-border) !important; margin: 20px 0 !important; }

/* ─── Text ──────────────────────────────────────────────────────── */
h1,h2,h3,h4 { color: var(--mm-text) !important; font-family: 'Inter', sans-serif !important; }
p, li, span { color: var(--mm-text) !important; }
.stMarkdown p { font-size: 13px !important; }
caption, .stCaption { color: var(--mm-text-muted) !important; font-size: 11px !important; }

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
  background: rgba(91,138,240,.15);
  border: 1px solid rgba(91,138,240,.4);
  color: #5B8AF0; font-size: 10px; font-weight: 700;
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
.mm-status-fresh  { background: var(--mm-success); box-shadow: 0 0 6px var(--mm-success); }
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

/* ─── Alert tweaks ──────────────────────────────────────────────── */
[data-testid="stAlert"] {
  border-radius: var(--mm-radius-sm) !important;
  font-size: 13px !important;
}
"""


def inject_css() -> None:
    """Inject the full MailMind CSS theme into the current Streamlit page."""
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)
