"""Streamlit review dashboard for MailMind.

Read‑only UI that displays predictions, actions, and sender reputations.
No body_text is ever exposed.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import streamlit as st

from mailmind.actions.executor import ActionExecutor
from mailmind.actions.safety import SafetyPolicy
from mailmind.ingestion.auth import authenticate, build_gmail_service
from mailmind.processing.scorer import ScoreResult
from mailmind.storage.database import Database
from mailmind.storage.models import Email as EmailModel
from mailmind.storage.queries import (
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

tab_overview, tab_predictions, tab_actions, tab_queue, tab_senders = st.tabs(
    [
        "Overview",
        "Prediction Detail",
        "Actions Log",
        f"Action Queue ({pending_count})",
        "Sender Reputation",
    ]
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
        # Colour-code pipeline_used column
        def _color_pipeline(val: str) -> str:
            if val == "hybrid":
                return "background-color: #d4edda"  # green
            elif val == "rules":
                return "background-color: #fff3cd"  # yellow
            return ""
        df = st.dataframe(filtered_preds, use_container_width=True)
        # Highlight hybrid rows using column configuration
        st.dataframe(
            filtered_preds,
            use_container_width=True,
            column_config={
                "pipeline_used": st.column_config.TextColumn(
                    "Pipeline",
                    help="rules = rules-only, hybrid = LLM contributed",
                ),
                "llm_confidence": st.column_config.NumberColumn(
                    "LLM Conf.",
                    help="Confidence score from DeepSeek LLM (0-1)",
                    format="%.2f",
                ),
                "ml_confidence": st.column_config.NumberColumn(
                    "ML Conf.",
                    help="Confidence score from local ML model (0-1)",
                    format="%.2f",
                ),
                "priority_score": st.column_config.NumberColumn(
                    "Score",
                    help="Priority score (0-100)",
                    format="%d",
                ),
            },
        )
    else:
        st.info("No predictions match the current filters.")


# ---------------------------------------------------------------------------
# Tab 2 – Prediction Detail
# ---------------------------------------------------------------------------

with tab_predictions:
    st.header("Prediction Detail")

    all_preds = get_recent_predictions(db, limit=200)
    email_gmail_ids = sorted({p["email_gmail_id"] for p in all_preds})
    selected_email = st.selectbox("Select email (email_gmail_id)", [""] + email_gmail_ids)

    if selected_email:
        preds = get_predictions_for_email(db, selected_email)
        filtered_preds = _apply_filters(preds)
        if filtered_preds:
            st.dataframe(
                filtered_preds,
                use_container_width=True,
                column_config={
                    "pipeline_used": st.column_config.TextColumn(
                        "Pipeline",
                        help="rules = rules-only, hybrid = LLM contributed",
                    ),
                    "llm_confidence": st.column_config.NumberColumn(
                        "LLM Conf.",
                        format="%.2f",
                    ),
                    "ml_confidence": st.column_config.NumberColumn(
                        "ML Conf.",
                        format="%.2f",
                    ),
                    "priority_score": st.column_config.NumberColumn(
                        "Score",
                        format="%d",
                    ),
                },
            )
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
        st.dataframe(filtered_actions, use_container_width=True)
    else:
        st.info("No actions match the current filters.")


# ---------------------------------------------------------------------------
# Tab 4 – Action Queue
# ---------------------------------------------------------------------------

with tab_queue:
    st.header("Pending Actions — Human Review Required")

    # Manual refresh button
    if st.button("🔄 Refresh"):
        st.rerun()

    if not pending_queue:
        st.success("🎉 Action queue is empty! All actions have been reviewed.")
    else:
        st.info(
            f"There are **{pending_count}** pending action(s) waiting for your review. "
            "Approve to execute immediately, or reject to skip."
        )

        for item in pending_queue:
            with st.container(border=True):
                cols = st.columns([2, 1, 1, 1, 1])

                with cols[0]:
                    st.markdown(
                        f"**Email:** `{item['email_gmail_id']}`  \n"
                        f"**Action:** `{item['suggested_action']}`"
                    )

                with cols[1]:
                    st.markdown(f"**Label:** `{item['primary_label']}`")

                with cols[2]:
                    conf = item.get("confidence", 0)
                    st.markdown(f"**Confidence:** `{conf:.2f}`")

                with cols[3]:
                    created_dt = datetime.fromtimestamp(
                        item["created_at"], tz=timezone.utc
                    )
                    st.markdown(
                        f"**Queued:** {created_dt.strftime('%Y-%m-%d %H:%M')}"
                    )

                with cols[4]:
                    approve_key = f"approve-{item['id']}"
                    reject_key = f"reject-{item['id']}"

                    if st.button("✅ Approve", key=approve_key):
                        try:
                            # Build the email object from the stored row
                            email_row = db.get_email_by_gmail_id(
                                item["email_gmail_id"]
                            )
                            if not email_row:
                                st.error(
                                    f"Email {item['email_gmail_id']} not found in DB."
                                )
                            else:
                                # Fetch prediction to rebuild ScoreResult
                                pred_row = db.execute_sql(
                                    "SELECT * FROM predictions WHERE id = ?",
                                    (item["prediction_id"],),
                                ).fetchone()
                                if not pred_row:
                                    st.error(
                                        f"Prediction {item['prediction_id']} not found."
                                    )
                                elif not pred_row["scoring_breakdown"]:
                                    st.error(
                                        f"Prediction {item['prediction_id']} has no scoring_breakdown."
                                    )
                                else:
                                    # Reconstruct ScoreResult from stored JSON
                                    score_data = json.loads(
                                        pred_row["scoring_breakdown"]
                                    )
                                    valid_fields = {
                                        f.name for f in fields(ScoreResult)
                                    }
                                    filtered = {
                                        k: v
                                        for k, v in score_data.items()
                                        if k in valid_fields
                                    }
                                    score_result = ScoreResult(**filtered)

                                    # Build Email model (safe subset, no body_text)
                                    email = EmailModel(
                                        gmail_id=email_row["gmail_id"],
                                        thread_id=email_row["thread_id"],
                                        sender=email_row["sender"],
                                        recipients=(
                                            email_row["recipients"].split(",")
                                            if email_row["recipients"]
                                            else []
                                        ),
                                        subject=email_row["subject"],
                                        labels=(
                                            email_row["labels"].split(",")
                                            if email_row["labels"]
                                            else []
                                        ),
                                    )

                                    # Execute the action in live mode
                                    executor = get_action_executor()
                                    success = executor.execute_action(
                                        email,
                                        item["suggested_action"],
                                        score_result,
                                    )

                                    # Mark as approved regardless of execution success
                                    approve_queue_item(db, item["id"])
                                    if success:
                                        st.success(
                                            f"✅ Approved & executed "
                                            f"'{item['suggested_action']}' on "
                                            f"{item['email_gmail_id']}"
                                        )
                                    else:
                                        st.warning(
                                            f"Approved '{item['suggested_action']}' for "
                                            f"{item['email_gmail_id']}, "
                                            "but execution may have been blocked "
                                            "(dry-run or policy)."
                                        )
                                    st.rerun()

                        except Exception as exc:
                            st.error(f"Error during approval: {exc}")

                    if st.button("❌ Reject", key=reject_key):
                        # Store the rejecting item ID in session state
                        st.session_state["rejecting_item_id"] = item["id"]
                        st.rerun()

                # Inline rejection form
                if (
                    st.session_state.get("rejecting_item_id") == item["id"]
                ):
                    with st.form(key=f"reject-form-{item['id']}"):
                        st.markdown("**Reason for rejection (optional):**")
                        corrected_label = st.text_input(
                            "Corrected label",
                            value="",
                            placeholder="e.g. WORK, PERSONAL, NEWSLETTER",
                        )
                        col_submit, col_cancel = st.columns(2)
                        with col_submit:
                            submitted = st.form_submit_button(
                                "Confirm Reject", type="primary"
                            )
                        with col_cancel:
                            cancelled = st.form_submit_button("Cancel")

                        if submitted:
                            # Reject the queue item
                            reject_queue_item(db, item["id"])

                            # If user provided a corrected label, log it
                            if corrected_label:
                                log_correction(
                                    db,
                                    email_gmail_id=item["email_gmail_id"],
                                    original_label=item["primary_label"],
                                    corrected_label=corrected_label,
                                    original_action=item["suggested_action"],
                                )

                            # Clear the rejecting state
                            del st.session_state["rejecting_item_id"]
                            st.warning(
                                f"❌ Rejected '{item['suggested_action']}' for "
                                f"{item['email_gmail_id']}"
                            )
                            st.rerun()

                        if cancelled:
                            del st.session_state["rejecting_item_id"]
                            st.rerun()


# ---------------------------------------------------------------------------
# Tab 5 – Sender Reputation
# ---------------------------------------------------------------------------

with tab_senders:
    st.header("Sender Reputation")
    senders = get_sender_reputations(db)
    if senders:
        st.dataframe(senders, use_container_width=True)
    else:
        st.info("No sender reputation records found.")
