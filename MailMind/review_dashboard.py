"""Streamlit review dashboard for MailMind.

Read‑only UI that displays predictions, actions, and sender reputations.
No body_text is ever exposed.
"""

from __future__ import annotations

import streamlit as st
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from mailmind.storage.database import Database
from mailmind.storage.queries import (
    get_recent_predictions,
    get_predictions_for_email,
    get_recent_actions,
    get_sender_reputations,
    get_summary_metrics,
)


# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="MailMind Review Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📬 MailMind Review Dashboard")
st.markdown("Read‑only view of predictions, actions, and sender reputations.")


# ---------------------------------------------------------------------------
# Database connection (cached)
# ---------------------------------------------------------------------------

@st.cache_resource
def get_db() -> Database:
    """Return a Database instance (cached for the session)."""
    # Use the default path; user can override via environment variable
    import os
    db_path = os.environ.get("MAILMIND_DB_PATH", "~/.mailmind/mailmind.db")
    return Database(db_path)


db = get_db()


# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

st.sidebar.header("Filters")

# Date range filter (default: last 7 days)
today = datetime.utcnow()
default_start = today - timedelta(days=7)
start_date = st.sidebar.date_input("Start date", value=default_start)
end_date = st.sidebar.date_input("End date", value=today)

# Pipeline used filter
pipeline_options = ["all", "rules_only", "ml", "llm"]
pipeline_filter = st.sidebar.selectbox("Pipeline used", pipeline_options)

# Label filter
label_options = ["all", "INBOX", "URGENT", "FINANCE", "PERSONAL", "NEWSLETTER", "CALENDAR", "OTHER"]
label_filter = st.sidebar.selectbox("Label", label_options)


# ---------------------------------------------------------------------------
# Helper to apply filters to a list of dicts
# ---------------------------------------------------------------------------

def _apply_filters(
    items: List[Dict[str, Any]],
    date_field: str = "created_at",
) -> List[Dict[str, Any]]:
    """Filter *items* by sidebar date range, pipeline, and label."""
    filtered = []
    for item in items:
        # Date filter
        ts = item.get(date_field)
        if ts is not None:
            try:
                item_date = datetime.utcfromtimestamp(ts)
            except (TypeError, OSError):
                item_date = None
            if item_date is not None:
                if item_date.date() < start_date or item_date.date() > end_date:
                    continue

        # Pipeline filter
        if pipeline_filter != "all":
            if item.get("pipeline_used") != pipeline_filter:
                continue

        # Label filter
        if label_filter != "all":
            if item.get("primary_label") != label_filter:
                continue

        filtered.append(item)
    return filtered


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_overview, tab_predictions, tab_actions, tab_senders = st.tabs(
    ["Overview", "Prediction Detail", "Actions Log", "Sender Reputation"]
)


# ---------------------------------------------------------------------------
# Tab 1 – Overview
# ---------------------------------------------------------------------------

with tab_overview:
    st.header("Summary Metrics")
    metrics = get_summary_metrics(db)
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Emails", metrics["emails"])
    col2.metric("Total Predictions", metrics["predictions"])
    col3.metric("Total Actions", metrics["actions"])

    st.subheader("Recent Predictions (last 10)")
    recent_preds = get_recent_predictions(db, limit=10)
    filtered_preds = _apply_filters(recent_preds)
    if filtered_preds:
        st.dataframe(filtered_preds, width='stretch')
    else:
        st.info("No predictions match the current filters.")


# ---------------------------------------------------------------------------
# Tab 2 – Prediction Detail
# ---------------------------------------------------------------------------

with tab_predictions:
    st.header("Prediction Detail")

    # Allow user to select an email_gmail_id
    all_preds = get_recent_predictions(db, limit=200)
    email_gmail_ids = sorted({p["email_gmail_id"] for p in all_preds})
    selected_email = st.selectbox("Select email (email_gmail_id)", [""] + email_gmail_ids)

    if selected_email:
        preds = get_predictions_for_email(db, selected_email)
        filtered_preds = _apply_filters(preds)
        if filtered_preds:
            st.dataframe(filtered_preds, width='stretch')
        else:
            st.info("No predictions match the current filters.")
    else:
        st.info("Select a gmail_id above to see its predictions.")


# ---------------------------------------------------------------------------
# Tab 3 – Actions Log
# ---------------------------------------------------------------------------

with tab_actions:
    st.header("Actions Log")
    actions = get_recent_actions(db, limit=200)
    filtered_actions = _apply_filters(actions)
    if filtered_actions:
        st.dataframe(filtered_actions, width='stretch')
    else:
        st.info("No actions match the current filters.")


# ---------------------------------------------------------------------------
# Tab 4 – Sender Reputation
# ---------------------------------------------------------------------------

with tab_senders:
    st.header("Sender Reputation")
    senders = get_sender_reputations(db)
    if senders:
        st.dataframe(senders, width='stretch')
    else:
        st.info("No sender reputation records found.")
