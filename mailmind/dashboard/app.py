"""MailMind Dashboard — Streamlit web UI.

Three-tab interface:
  NOW      — high-priority / reply-needed items, single Approve action
  REVIEW   — all pending items with full reasoning and Approve/Reject/Edit
  AUTOMATE — sender profiles, model health, queue statistics
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from mailmind.config import MailMindConfig
from mailmind.dashboard.helpers import (
    filter_now_items,
    format_unix_ts,
    get_confidence_badge,
    get_time_ago_str,
    parse_reason_json,
)
from mailmind.intelligence.feedback import handle_approve, handle_correction, handle_reject
from mailmind.processing.queue_manager import QueueManager
from mailmind.storage.database import Database
from mailmind.storage.queries import (
    get_ml_model_metadata,
    get_pending_queue_enriched,
    get_queue_stats,
    get_sender_profiles,
    toggle_sender_auto_action,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="MailMind Dashboard",
    page_icon="📧",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# DB initialisation
# ---------------------------------------------------------------------------


@st.cache_resource
def get_db() -> Database:
    config = MailMindConfig.from_env()
    db_path = Path(config.data_dir) / "mailmind.db"
    return Database(db_path)


# ---------------------------------------------------------------------------
# Tab 1: NOW
# ---------------------------------------------------------------------------


def render_now_tab() -> None:
    st.header("📍 NOW")
    st.markdown("_Items requiring immediate attention_")

    db = get_db()
    all_items = get_pending_queue_enriched(db, limit=200)
    now_items = filter_now_items(all_items, queue_threshold=QueueManager.QUEUE_THRESHOLD)

    if not now_items:
        st.info("✅ No high-priority items right now — you're all caught up.")
        return

    st.metric("High Priority Items", len(now_items))

    for idx, item in enumerate(now_items):
        item_id = item.get('id')
        subject = (item.get('subject') or '[No Subject]')[:80]
        reason = parse_reason_json(item.get('reason_json'))

        with st.container(border=True):
            col_meta, col_badge = st.columns([3, 1])

            with col_meta:
                sender_display = item.get('display_name') or item.get('sender') or 'Unknown'
                st.markdown(f"**From:** {sender_display}")
                st.markdown(f"**Subject:** {subject}")
                st.caption(f"Received: {get_time_ago_str(item.get('created_at'))}")

            with col_badge:
                conf = item.get('confidence') or 0.0
                label = item.get('primary_label') or 'Unclassified'
                badge = get_confidence_badge(conf)
                st.markdown(f"**Label:** {label} {badge}")
                st.markdown(f"**Confidence:** {conf:.2%}")

            # Thread summary
            if reason.get('thread_summary'):
                st.markdown(f"**Thread:** _{reason['thread_summary']}_")

            # Reply-needed pill
            if reason.get('reply_needed'):
                st.markdown("🗨️ **Reply Needed**")

            # Single Approve button (NOW tab is action-focused, not review-focused)
            if st.button("✅ Approve", key=f"now_approve_{idx}_{item_id}"):
                acted = handle_approve(db, item_id)
                if acted:
                    st.toast(f"✅ Approved: {subject[:50]}", icon="✅")
                    st.rerun()
                else:
                    st.warning(
                        "This item was already processed or no longer exists "
                        "(possibly approved in another tab)."
                    )


# ---------------------------------------------------------------------------
# Tab 2: REVIEW
# ---------------------------------------------------------------------------


def render_review_tab() -> None:
    st.header("📋 REVIEW")
    st.markdown("_All pending queue items with detailed reasoning_")

    db = get_db()
    items = get_pending_queue_enriched(db)

    if not items:
        st.info("✅ No pending items!")
        return

    st.metric("Pending Items", len(items))

    for idx, item in enumerate(items):
        item_id = item.get('id')
        subject = (item.get('subject') or '[No Subject]')[:60]
        sender = (item.get('sender') or 'Unknown')[:30]

        with st.expander(f"📧 {sender} — {subject}", expanded=False):
            # --- Summary row ---
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown(f"**Sender:** {item.get('sender', 'Unknown')}")
                st.markdown(f"**Action:** {item.get('action', 'N/A')}")
            with col2:
                conf = item.get('confidence') or 0.0
                badge = get_confidence_badge(conf)
                st.markdown(f"**Label:** {item.get('primary_label', 'Unclassified')} {badge}")
                st.markdown(f"**Confidence:** {conf:.2%}")
            with col3:
                st.markdown(f"**Priority:** {item.get('priority_score', 0)}")
                st.markdown(f"**Status:** {item.get('status', 'pending')}")

            # --- Why this? ---
            st.markdown("### 🤔 Why this?")
            reason = parse_reason_json(item.get('reason_json'))

            if reason.get('rule_matches'):
                st.markdown("**Rule Matches:**")
                for rule in reason['rule_matches']:
                    st.markdown(f" • {rule}")

            if reason.get('score_breakdown'):
                st.markdown("**Score Breakdown:**")
                breakdown = reason['score_breakdown']
                if isinstance(breakdown, str):
                    breakdown = parse_reason_json(breakdown)
                for k, v in breakdown.items():
                    st.markdown(f" • **{k}:** {v}")

            ml_conf = reason.get('ml_confidence') or item.get('ml_confidence')
            if ml_conf is not None:
                st.markdown(f"**ML Confidence:** {float(ml_conf):.2%}")

            llm_conf = reason.get('llm_confidence') or item.get('llm_confidence')
            if llm_conf is not None:
                st.markdown(f"**LLM Confidence:** {float(llm_conf):.2%}")

            trust_tier = item.get('trust_tier') or reason.get('trust_tier') or 'neutral'
            trust_icon = {'trusted': '✅', 'neutral': '⚠️', 'watchlist': '🚫'}.get(trust_tier, '⚠️')
            st.markdown(f"**Trust Tier:** {trust_icon} {trust_tier}")

            if reason.get('thread_summary'):
                st.markdown(f"**Thread Summary:** {reason['thread_summary']}")

            # Key name from explainer is `similar_past_actions`
            past = reason.get('similar_past_actions') or []
            if past:
                st.markdown("**Similar Past Actions:**")
                for entry in past:
                    action_label = entry.get('action') or str(entry)
                    st.markdown(f" • {action_label}")

            st.markdown("---")
            st.markdown(f"**Created:** {format_unix_ts(item.get('created_at'))}")
            st.markdown(f"**Updated:** {format_unix_ts(item.get('updated_at'))}")

            # --- Action buttons ---
            col_approve, col_reject, col_edit = st.columns(3)

            with col_approve:
                if st.button("✅ Approve", key=f"review_approve_{idx}_{item_id}"):
                    acted = handle_approve(db, item_id)
                    if acted:
                        st.toast("✅ Approved", icon="✅")
                        st.rerun()
                    else:
                        st.warning("Item already processed or no longer exists.")

            with col_reject:
                if st.button("❌ Reject", key=f"review_reject_{idx}_{item_id}"):
                    acted = handle_reject(db, item_id)
                    if acted:
                        st.toast("❌ Rejected", icon="❌")
                        st.rerun()
                    else:
                        st.warning("Item already processed or no longer exists.")

            with col_edit:
                if st.button("✏️ Edit Label", key=f"review_edit_{idx}_{item_id}"):
                    st.session_state[f"edit_review_{item_id}"] = True

            if st.session_state.get(f"edit_review_{item_id}"):
                st.markdown("**Change Label:**")
                col_sel, col_save = st.columns(2)
                with col_sel:
                    new_label = st.selectbox(
                        "New Label",
                        options=["IMPORTANT", "WORK", "PERSONAL", "NEWSLETTER",
                                 "NOTIFICATION", "SPAM", "DEFER"],
                        key=f"review_label_select_{item_id}",
                    )
                with col_save:
                    if st.button("Save", key=f"review_save_label_{item_id}"):
                        acted = handle_correction(db, item_id, corrected_label=new_label)
                        if acted:
                            st.toast(f"✏️ Label corrected → {new_label}", icon="✏️")
                            st.session_state.pop(f"edit_review_{item_id}", None)
                            st.rerun()
                        else:
                            st.warning("Item no longer exists.")


# ---------------------------------------------------------------------------
# Tab 3: AUTOMATE
# ---------------------------------------------------------------------------


def render_automate_tab() -> None:
    st.header("⚙️ AUTOMATE")

    db = get_db()

    # --- Section 1: Sender Profiles ---
    st.subheader("📧 Sender Profiles")
    profiles = get_sender_profiles(db)

    if profiles:
        df = pd.DataFrame(profiles)[
            ['sender_email', 'trust_tier', 'total_seen', 'total_approved',
             'total_rejected', 'approval_rate', 'auto_action_eligible']
        ]
        st.dataframe(df, use_container_width=True)

        st.markdown("**Toggle Auto-Action Eligibility:**")
        for profile in profiles:
            email_key = profile['sender_email']
            col_label, col_toggle = st.columns([4, 1])
            with col_label:
                tier_icon = {'trusted': '✅', 'neutral': '⚠️', 'watchlist': '🚫'}.get(
                    profile.get('trust_tier', 'neutral'), '⚠️'
                )
                st.caption(f"{tier_icon} {email_key}")
            with col_toggle:
                current = bool(profile['auto_action_eligible'])
                new_val = st.toggle(
                    "Auto",
                    value=current,
                    key=f"auto_action_{email_key}",
                )
                if new_val != current:
                    toggle_sender_auto_action(db, email_key, new_val)
                    state_str = "enabled" if new_val else "disabled"
                    st.toast(f"Auto-action {state_str} for {email_key}", icon="✅")
    else:
        st.info("No sender profiles yet. They appear as you approve/reject items.")

    st.markdown("---")

    # --- Section 2: Model Health ---
    st.subheader("🤖 Model Health")
    model_meta = get_ml_model_metadata(db)

    if model_meta:
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Last Trained", format_unix_ts(model_meta.get('created_at')))
        with c2:
            acc = model_meta.get('accuracy')
            st.metric("Accuracy", f"{acc:.2%}" if acc else "N/A")
        with c3:
            st.metric("Training Samples", model_meta.get('training_samples', 0))
    else:
        st.info(
            "❓ No model trained yet.\n\n"
            "Run: `python -m mailmind.scripts.train_ml_model`"
        )

    st.markdown("---")

    # --- Section 3: Queue Statistics ---
    st.subheader("📊 Queue Statistics")
    stats = get_queue_stats(db)

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Pending", stats.get('pending', 0))
    with c2:
        st.metric("Approved", stats.get('approved', 0))
    with c3:
        st.metric("Rejected", stats.get('rejected', 0))
    with c4:
        st.metric("Executed", stats.get('executed', 0))
    with c5:
        st.metric("Failed", stats.get('failed', 0))

    reply_needed = stats.get('reply_needed_pending', 0)
    if reply_needed > 0:
        st.warning(f"⚠️ {reply_needed} pending item(s) flagged **Reply Needed**")

    st.markdown(f"**Superseded:** {stats.get('superseded', 0)}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    st.title("🪃 MailMind Dashboard")
    st.markdown("_AI-powered email prioritisation and automation_")

    tab_now, tab_review, tab_automate = st.tabs(["📍 NOW", "📋 REVIEW", "⚙️ AUTOMATE"])

    with tab_now:
        render_now_tab()
    with tab_review:
        render_review_tab()
    with tab_automate:
        render_automate_tab()


if __name__ == "__main__":
    main()
