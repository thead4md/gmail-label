"""Streamlit review dashboard for MailMind.

Read‑only UI that displays predictions, actions, and sender reputations.
No body_text is ever exposed.
"""

from __future__ import annotations

import json
import os
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import streamlit as st

from mailmind.actions.executor import ActionExecutor
from mailmind.actions.safety import SafetyPolicy
from mailmind.ingestion.auth import authenticate, build_gmail_service
from mailmind.processing.scorer import ScoreResult
from mailmind.storage.database import Database
from mailmind.storage.models import Email as EmailModel
from mailmind.storage.queries import (
    get_recent_predictions_with_emails,
    get_recent_predictions,
    get_predictions_for_email,
    get_recent_actions,
    get_sender_reputations,
    get_summary_metrics,
    get_pending_queue,
    approve_queue_item,
    reject_queue_item,
    log_correction,
)
def _check_password():
    password = os.environ.get("DASHBOARD_PASSWORD", "")
    if not password:
        return  # no password set, skip gate (dev mode)
    if st.session_state.get("authenticated"):
        return
    pwd = st.text_input("Password", type="password")
    if st.button("Login"):
        if pwd == password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Wrong password")
    st.stop()

_check_password()
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

# Add a prominent DRY RUN banner when enabled
if os.environ.get("MAILMIND_DRY_RUN", "0") == "1":
    st.warning(
        "**DRY RUN MODE** — Actions approved in the dashboard will be logged but "
        "not executed on your Gmail account. Set MAILMIND_DRY_RUN=0 for live mode."
    )


# ---------------------------------------------------------------------------
# Database connection (cached)
# ---------------------------------------------------------------------------

@st.cache_resource
def get_db() -> Database:
    """Return a Database instance (cached for the session)."""
    import os
    db_path = os.environ.get("MAILMIND_DB_PATH", "~/.mailmind/mailmind.db")
    return Database(db_path)


db = get_db()


@st.cache_resource
def get_action_executor() -> ActionExecutor:
    """Build and cache an ActionExecutor for the dashboard.

    dry_run mode is controlled by the MAILMIND_DRY_RUN environment variable.
    When dry_run=True (default), actions are logged but not executed.
    Set MAILMIND_DRY_RUN=0 for live mode where approved actions execute.
    """
    creds = authenticate()
    service = build_gmail_service(creds)
    is_dry_run = os.environ.get("MAILMIND_DRY_RUN", "1") != "0"
    safety_policy = SafetyPolicy(dry_run=is_dry_run)
    return ActionExecutor(
        service=service,
        db=db,
        safety_policy=safety_policy,
    )


# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

st.sidebar.header("Filters")

# Date range filter (default: last 7 days)
today = datetime.now(timezone.utc)
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
                item_date = datetime.fromtimestamp(ts, tz=timezone.utc)
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

pending_queue = get_pending_queue(db)
pending_count = len(pending_queue)

tab_now, tab_review, tab_automate = st.tabs(["Now", "Review", "Automate"])


# ---------------------------------------------------------------------------
# Tab 1 – Now
# ---------------------------------------------------------------------------

from mailmind.storage.queries import get_pending_queue_enriched, get_pending_queue
from mailmind.intelligence.feedback import handle_approve, handle_reject, handle_correction
from .dashboard.helpers import filter_now_items

pending_enriched = get_pending_queue_enriched(db, limit=200)
now_items = filter_now_items(pending_enriched)

with tab_now:
    st.header("Now — Urgent & Reply Needed")
    if not now_items:
        st.info("No urgent items right now.")
    else:
        for item in now_items:
            email_row = db.get_email_by_gmail_id(item['email_gmail_id'])
            sender = email_row['sender'] if email_row else item['email_gmail_id']
            subject = email_row['subject'] if email_row else '(no subject)'
            col1, col2, col3 = st.columns([3,1,1])
            with col1:
                st.markdown(f"**{sender}** — {subject}")
                reason = item.get('reason') or item.get('reason_json') or {}
                if isinstance(reason, str):
                    try:
                        reason = json.loads(reason)
                    except Exception:
                        reason = {}
                if reason.get('thread_summary'):
                    st.caption(reason.get('thread_summary'))
                if reason.get('reply_needed'):
                    st.markdown(":red[Reply Needed]")
            with col2:
                conf = float(item.get('confidence') or 0)
                color = 'green' if conf > 0.8 else ('amber' if conf >= 0.5 else 'red')
                st.markdown(f"**Label:** {item.get('primary_label')}  ")
                st.markdown(f"**Confidence:** `{conf:.2f}`")
            with col3:
                if st.button("✅ Approve", key=f"now-approve-{item['id']}"):
                    try:
                        handle_approve(db, item['id'])
                        st.success("Approved")
                        st.experimental_rerun()
                    except Exception as exc:
                        st.error(f"Approve failed: {exc}")



# ---------------------------------------------------------------------------
# Tab 2 – Review
# ---------------------------------------------------------------------------

with tab_review:
    st.header("Recent Predictions")
    recent_preds = get_recent_predictions_with_emails(db, limit=200)
    filtered_preds = _apply_filters(recent_preds, date_field="date")
    if filtered_preds:
        st.dataframe(
            filtered_preds,
            use_container_width=True,
            column_config={
                "subject": st.column_config.TextColumn("Subject", width="large"),
                "sender": st.column_config.TextColumn("Sender"),
                "date": st.column_config.TextColumn("Date"),
                "preview": st.column_config.TextColumn("Preview", width="large"),
                "primary_label": st.column_config.TextColumn("Label"),
                "classifier_source": st.column_config.TextColumn("Source"),
                "confidence": st.column_config.NumberColumn("Confidence", format="%.2f"),
                "llm_rationale": st.column_config.TextColumn("LLM Rationale", width="medium"),
                "action_hint": st.column_config.TextColumn("Action Hint"),
            },
        )
    else:
        st.info("No predictions match the current filters.")

    # Pending action queue (items that need human approval)
    st.subheader(f"Action Queue ({pending_count} pending)")
    pending = get_pending_queue_enriched(db, limit=500)
    if not pending:
        st.info("No items pending approval.")
    else:
        for item in pending:
            email_row = db.get_email_by_gmail_id(item['email_gmail_id'])
            sender = email_row['sender'] if email_row else item['email_gmail_id']
            subject = email_row['subject'] if email_row else '(no subject)'
            st.markdown(f"**From:** {sender}  •  **Subject:** {subject}")
            cols = st.columns([1, 1, 1])
            with cols[0]:
                if st.button("✅ Approve", key=f"approve-{item['id']}"):
                    try:
                        handle_approve(db, item['id'])
                        st.success("Approved")
                        st.experimental_rerun()
                    except Exception as exc:
                        st.error(f"Approve failed: {exc}")
            with cols[1]:
                if st.button("❌ Reject", key=f"reject-{item['id']}"):
                    try:
                        handle_reject(db, item['id'])
                        st.warning("Rejected")
                        st.experimental_rerun()
                    except Exception as exc:
                        st.error(f"Reject failed: {exc}")
            with cols[2]:
                new_label = st.text_input("Correct label", key=f"label-input-{item['id']}", placeholder="e.g. WORK")
                if new_label:
                    if st.button("✏️ Save", key=f"edit-{item['id']}"):
                        try:
                            handle_correction(db, item['email_gmail_id'], item.get('primary_label'), new_label, item.get('suggested_action'), None)
                            st.success("Correction logged")
                            st.experimental_rerun()
                        except Exception as exc:
                            st.error(f"Correction failed: {exc}")

            with st.expander("Why this?"):
                reason = item.get('reason') or item.get('reason_json') or {}
                if isinstance(reason, str):
                    try:
                        reason = json.loads(reason)
                    except Exception:
                        reason = {}
                sb = reason.get('score_breakdown') or reason.get('scoring_breakdown') or {}
                if sb:
                    st.markdown("**Score breakdown**")
                    for k, v in (sb.items() if isinstance(sb, dict) else []):
                        st.write(f"{k}: {v}")
                tier = reason.get('trust_tier', 'neutral')
                icon = '✅' if tier == 'trusted' else ('🚫' if tier == 'watchlist' else '⚠️')
                st.write(f"Trust tier: {icon} {tier}")


# ---------------------------------------------------------------------------
# Tab 3 – Automate
# ---------------------------------------------------------------------------

with tab_automate:
    st.header("Automate — Sender Profiles & Model Health")
    st.subheader("Sender Profiles")
    sp_rows = db.execute_sql("SELECT * FROM sender_profiles").fetchall()
    df = [dict(r) for r in sp_rows]
    for r in df:
        r['approval_rate'] = (r['total_approved'] / r['total_seen']) if r['total_seen'] else None
    if df:
        st.dataframe(df, use_container_width=True)
        for row in df:
            if st.button(f"Toggle Auto-Action {row['sender_email']}"):
                new = 0 if row['auto_action_eligible'] else 1
                with db.transaction() as cur:
                    cur.execute("UPDATE sender_profiles SET auto_action_eligible = ? WHERE sender_email = ?", (new, row['sender_email']))
                st.success("Updated")
                st.experimental_rerun()
    else:
        st.info("No sender profiles yet.")

    st.subheader("Model Health")
    try:
        meta = db.execute_sql("SELECT * FROM ml_model_metadata ORDER BY trained_at DESC LIMIT 1").fetchone()
        if not meta:
            st.info("No model trained yet. Run python -m mailmind.scripts.train_ml_model")
        else:
            st.write(dict(meta))
    except Exception:
        st.info("No model metadata table found.")

    st.subheader("Queue Stats")
    stats = db.execute_sql("SELECT status, COUNT(*) as cnt FROM action_queue GROUP BY status").fetchall()
    st.write({r['status']: r['cnt'] for r in stats})
    pending_reply_count = db.execute_sql("SELECT COUNT(*) FROM action_queue WHERE status = 'pending' AND json_extract(reason_json, '$.reply_needed') = 1").fetchone()[0]
    st.write({"pending_reply_needed": pending_reply_count})
