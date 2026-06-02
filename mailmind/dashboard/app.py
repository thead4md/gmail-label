"""MailMind Dashboard — Streamlit web UI.

Three-tab interface:
  NOW      — high-priority / reply-needed items, single Approve action
  REVIEW   — all pending items with full reasoning and Approve/Reject/Edit
  AUTOMATE — activity digest, sender profiles, model health, queue stats

Design: dark-mode card layout (dashboard/theme.py), Altair charts for digest.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from mailmind.config import MailMindConfig
from mailmind.dashboard.helpers import (
    channel_chip_html,
    confidence_bar_html,
    email_card_html,
    filter_now_items,
    format_unix_ts,
    get_confidence_badge,
    get_heartbeat_status,
    get_time_ago_str,
    label_chip_html,
    parse_reason_json,
    reply_needed_pill_html,
    sender_avatar_html,
    trust_badge_html,
)
from mailmind.dashboard.theme import (
    LABEL_COLORS,
    channel_color,
    inject_css,
    label_color,
    trust_color,
)
from mailmind.intelligence.feedback import handle_approve, handle_correction, handle_reject
from mailmind.processing.queue_manager import QueueManager
from mailmind.storage.database import Database
from mailmind.storage.queries import (
    build_digest,
    get_ml_model_metadata,
    get_pending_queue_enriched,
    get_queue_stats,
    get_recent_predictions_with_emails,
    get_sender_profiles,
    toggle_sender_auto_action,
)

# ---------------------------------------------------------------------------
# Page config  (must be first Streamlit call)
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="MailMind",
    page_icon="📧",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# DB & executor
# ---------------------------------------------------------------------------


@st.cache_resource
def get_db() -> Database:
    import os
    db_path = Path(os.environ.get("MAILMIND_DB_PATH", "~/.mailmind/mailmind.db")).expanduser()
    return Database(db_path)


def get_accounts() -> List[str]:
    return MailMindConfig.load_accounts()


@st.cache_resource
def get_action_executor():
    """Build a real ActionExecutor when credentials are available, else None."""
    import os
    from mailmind.actions.executor import ActionExecutor
    from mailmind.actions.safety import SafetyPolicy
    from mailmind.ingestion.auth import build_gmail_service, load_stored_credentials

    creds = load_stored_credentials()
    if creds is None:
        return None
    service = build_gmail_service(creds)
    dry_run = os.environ.get("MAILMIND_DRY_RUN", "0") == "1"
    return ActionExecutor(service=service, db=get_db(), safety_policy=SafetyPolicy(dry_run=dry_run))


# ---------------------------------------------------------------------------
# Section header helper
# ---------------------------------------------------------------------------

def _section(icon: str, title: str) -> None:
    st.markdown(
        f'<div class="mm-section-header">{icon} {title}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Tab 1: NOW
# ---------------------------------------------------------------------------


def render_now_tab(account: Optional[str] = None) -> None:
    db       = get_db()
    all_items = get_pending_queue_enriched(db, limit=200, account=account)
    now_items = filter_now_items(all_items, queue_threshold=QueueManager.QUEUE_THRESHOLD)

    if not now_items:
        st.markdown(
            '<div class="mm-empty">'
            '<div class="mm-empty-icon">✅</div>'
            '<div class="mm-empty-text">You\'re all caught up</div>'
            '<div class="mm-empty-sub">No high-priority or reply-needed items right now</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    c_count, c_spacer = st.columns([1, 4])
    with c_count:
        st.metric("Needs attention", len(now_items))

    st.markdown("")

    for idx, item in enumerate(now_items):
        item_id  = item.get("id")
        subject  = (item.get("subject") or "[No Subject]")[:80]
        sender   = item.get("sender") or "Unknown"
        label    = item.get("primary_label")
        channel  = item.get("channel")
        conf     = item.get("confidence") or 0.0
        reason   = parse_reason_json(item.get("reason_json"))
        reply_needed  = bool(reason.get("reply_needed"))
        thread_summary = reason.get("thread_summary")
        time_ago = get_time_ago_str(item.get("created_at"))

        # Visual card (display only)
        st.markdown(
            email_card_html(
                subject=subject,
                sender=sender,
                time_ago=time_ago,
                label=label,
                channel=channel,
                confidence=conf,
                reply_needed=reply_needed,
                thread_summary=thread_summary,
            ),
            unsafe_allow_html=True,
        )

        # Approve button below the card
        col_btn, col_spacer = st.columns([1, 5])
        with col_btn:
            st.markdown('<div class="mm-btn-approve">', unsafe_allow_html=True)
            if st.button("✅ Approve", key=f"now_approve_{idx}_{item_id}"):
                acted = handle_approve(db, item_id, executor=get_action_executor())
                if acted:
                    st.toast(f"✅ {subject[:50]}", icon="✅")
                    st.rerun()
                else:
                    st.warning("Already processed or no longer exists.")
            st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Tab 2: REVIEW
# ---------------------------------------------------------------------------


def _render_reason_panel(reason: Dict[str, Any], item: Dict[str, Any]) -> None:
    """Render the 'Why this?' structured panel."""
    rows: List[tuple[str, str]] = []

    lbl = item.get("primary_label")
    if lbl:
        rows.append(("Label", label_chip_html(lbl)))

    conf = item.get("confidence")
    if conf is not None:
        rows.append(("Confidence", confidence_bar_html(conf)))

    tier = item.get("trust_tier") or reason.get("trust_tier")
    if tier:
        rows.append(("Sender trust", trust_badge_html(tier)))

    ch = item.get("channel")
    if ch:
        rows.append(("Channel", channel_chip_html(ch)))

    if reason.get("rule_matches"):
        rules_html = " ".join(
            f'<code style="font-size:11px;background:#1C2237;padding:1px 5px;'
            f'border-radius:3px;">{r}</code>'
            for r in reason["rule_matches"]
        )
        rows.append(("Rules matched", rules_html))

    ml_conf = reason.get("ml_confidence") or item.get("ml_confidence")
    if ml_conf is not None:
        rows.append(("ML confidence", confidence_bar_html(float(ml_conf))))

    llm_conf = reason.get("llm_confidence") or item.get("llm_confidence")
    if llm_conf is not None:
        rows.append(("LLM confidence", confidence_bar_html(float(llm_conf))))

    if reason.get("thread_summary"):
        rows.append(("Thread", f'<em style="color:#94A3B8;">{reason["thread_summary"][:150]}</em>'))

    past = reason.get("similar_past_actions") or []
    if past:
        actions_html = " ".join(
            f'<code style="font-size:11px;background:#1C2237;padding:1px 5px;border-radius:3px;">'
            f'{e.get("action", str(e))}</code>'
            for e in past[:5]
        )
        rows.append(("Past actions", actions_html))

    if not rows:
        return

    rows_html = "".join(
        f'<div class="mm-reason-row">'
        f'<span class="mm-reason-key">{k}</span>'
        f'<span class="mm-reason-val">{v}</span>'
        f'</div>'
        for k, v in rows
    )
    st.markdown(
        f'<div class="mm-reason-panel">{rows_html}</div>',
        unsafe_allow_html=True,
    )


def render_review_tab(account: Optional[str] = None) -> None:
    db = get_db()

    # ── Recent predictions ───────────────────────────────────────────
    _section("📊", "Recent predictions")
    preds = get_recent_predictions_with_emails(db, limit=200, account=account)
    if preds:
        df = pd.DataFrame(preds)
        df["date"] = df["date"].apply(lambda ts: format_unix_ts(ts) if ts else "")
        df = df.drop(columns=["preview", "email_gmail_id"], errors="ignore")
        st.dataframe(df, use_container_width=True, height=220)
    else:
        st.info("No predictions yet — run the pipeline to classify emails.")

    # ── Pending approval ─────────────────────────────────────────────
    items = get_pending_queue_enriched(db, account=account)
    _section("⏳", f"Pending approval — {len(items)} items")

    if not items:
        st.markdown(
            '<div class="mm-empty">'
            '<div class="mm-empty-icon">✅</div>'
            '<div class="mm-empty-text">Queue is clear</div>'
            '<div class="mm-empty-sub">All actions have been reviewed</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    for idx, item in enumerate(items):
        item_id = item.get("id")
        subject = (item.get("subject") or "[No Subject]")[:60]
        sender  = (item.get("sender")  or "Unknown")[:30]
        label   = item.get("primary_label")
        conf    = item.get("confidence") or 0.0
        lbl_color = label_color((label or "").upper())

        # Expander header with inline chips
        header_html = (
            f'📧 <b>{sender}</b> — {subject} '
            f'{label_chip_html(label)} '
            f'<span style="font-size:11px;color:#94A3B8;">{get_time_ago_str(item.get("created_at"))}</span>'
        )

        with st.expander(f"📧 {sender} — {subject}", expanded=False):
            # Top strip: label | action | priority
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown(f"**Sender:** {item.get('sender','Unknown')}")
                st.markdown(f"**Action:** `{item.get('action','N/A')}`")
            with col2:
                st.markdown(
                    f"**Label:** {label_chip_html(label)}&nbsp;&nbsp;{confidence_bar_html(conf)}",
                    unsafe_allow_html=True,
                )
                ch = item.get("channel")
                if ch:
                    st.markdown(channel_chip_html(ch), unsafe_allow_html=True)
            with col3:
                st.markdown(f"**Priority:** {item.get('priority_score', 0)} / 100")
                st.markdown(f"**Created:** {format_unix_ts(item.get('created_at'))}")

            # Why this?
            st.markdown(
                '<div class="mm-section-header" style="margin-top:12px;">🤔 Why this?</div>',
                unsafe_allow_html=True,
            )
            reason = parse_reason_json(item.get("reason_json"))
            _render_reason_panel(reason, item)

            # Action buttons
            st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
            col_approve, col_reject, col_edit = st.columns(3)

            with col_approve:
                st.markdown('<div class="mm-btn-approve">', unsafe_allow_html=True)
                if st.button("✅ Approve", key=f"review_approve_{idx}_{item_id}"):
                    acted = handle_approve(db, item_id, executor=get_action_executor())
                    if acted:
                        st.toast("✅ Approved", icon="✅")
                        st.rerun()
                    else:
                        st.warning("Already processed or no longer exists.")
                st.markdown("</div>", unsafe_allow_html=True)

            with col_reject:
                st.markdown('<div class="mm-btn-reject">', unsafe_allow_html=True)
                if st.button("❌ Reject", key=f"review_reject_{idx}_{item_id}"):
                    acted = handle_reject(db, item_id)
                    if acted:
                        st.toast("❌ Rejected", icon="❌")
                        st.rerun()
                    else:
                        st.warning("Already processed or no longer exists.")
                st.markdown("</div>", unsafe_allow_html=True)

            with col_edit:
                if st.button("✏️ Edit label", key=f"review_edit_{idx}_{item_id}"):
                    st.session_state[f"edit_review_{item_id}"] = True

            if st.session_state.get(f"edit_review_{item_id}"):
                col_sel, col_save = st.columns(2)
                with col_sel:
                    new_label = st.selectbox(
                        "New label",
                        options=list(LABEL_COLORS.keys()),
                        key=f"review_label_select_{item_id}",
                    )
                with col_save:
                    if st.button("Save", key=f"review_save_label_{item_id}"):
                        acted = handle_correction(db, item_id, corrected_label=new_label)
                        if acted:
                            st.toast(f"✏️ → {new_label}", icon="✏️")
                            st.session_state.pop(f"edit_review_{item_id}", None)
                            st.rerun()
                        else:
                            st.warning("Item no longer exists.")


# ---------------------------------------------------------------------------
# Tab 3: AUTOMATE
# ---------------------------------------------------------------------------


def _digest_chart(digest: Dict[str, Any]) -> None:
    """Render an Altair bar chart for the top-labels section."""
    top = digest.get("top_labels") or []
    if not top:
        return
    import altair as alt

    df = pd.DataFrame(top)  # columns: label, count
    # Map label → colour
    df["color"] = df["label"].apply(lambda l: label_color(l.upper()))

    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("label:N", sort="-y",
                    axis=alt.Axis(labelColor="#94A3B8", labelFontSize=11,
                                  tickColor="transparent", domainColor="#2D3656",
                                  title=None)),
            y=alt.Y("count:Q",
                    axis=alt.Axis(labelColor="#94A3B8", labelFontSize=10,
                                  gridColor="#1C2237", domainColor="transparent",
                                  title=None)),
            color=alt.Color("color:N", scale=None, legend=None),
            tooltip=["label", "count"],
        )
        .properties(height=140, background="transparent")
        .configure_view(strokeWidth=0)
    )
    st.altair_chart(chart, use_container_width=True)


def render_automate_tab(account: Optional[str] = None) -> None:
    import time as _time
    db = get_db()

    # ── Activity digest ─────────────────────────────────────────────
    _section("📊", "Activity digest")

    col_win, _ = st.columns([1, 3])
    with col_win:
        digest_days = st.slider("Window (days)", 1, 30, 7, key="digest_days")

    since_ts = int(_time.time()) - digest_days * 86400
    digest   = build_digest(db, since_ts=since_ts, account=account)

    d1, d2, d3, d4, d5 = st.columns(5)
    with d1: st.metric("Classified",  digest["classified"])
    with d2: st.metric("Executed",    digest["executed"])
    with d3: st.metric("In queue",    digest["queued"],
                        help="Currently pending — not window-scoped")
    with d4: st.metric("Corrections", digest["corrections"])
    with d5:
        failed = digest.get("execute_failed", 0)
        st.metric("Exec errors", failed,
                  delta=f"{failed} errors" if failed else None,
                  delta_color="inverse")

    if digest.get("pending_reply_needed"):
        st.info(f"💬 {digest['pending_reply_needed']} pending item(s) flagged Reply Needed")
    if digest.get("execute_failed"):
        st.warning(f"⚠️ {digest['execute_failed']} execution errors — check the watcher logs")

    _digest_chart(digest)

    # ── Sender profiles ─────────────────────────────────────────────
    _section("👤", "Sender profiles")
    profiles = get_sender_profiles(db)

    if profiles:
        # Summary dataframe
        df = pd.DataFrame(profiles)[[
            "sender_email", "trust_tier", "total_seen", "total_approved",
            "total_rejected", "approval_rate", "auto_action_eligible",
        ]]
        st.dataframe(df, use_container_width=True, height=240)

        # Per-sender autopilot toggles — card layout
        st.markdown(
            '<div class="mm-section-header" style="margin-top:20px;">⚡ Autopilot eligibility</div>',
            unsafe_allow_html=True,
        )
        for profile in profiles:
            email_key = profile["sender_email"]
            tier      = profile.get("trust_tier", "neutral")
            col_info, col_stats, col_toggle = st.columns([3, 3, 1])

            with col_info:
                st.markdown(
                    f'{trust_badge_html(tier)}&nbsp;&nbsp;'
                    f'<span style="font-size:13px;">{email_key}</span>',
                    unsafe_allow_html=True,
                )
            with col_stats:
                appr = profile.get("total_approved", 0)
                rej  = profile.get("total_rejected", 0)
                rate = profile.get("approval_rate", 0.0)
                st.markdown(
                    f'<span style="font-size:12px;color:#94A3B8;">'
                    f'✅ {appr} approved &nbsp; ❌ {rej} rejected &nbsp; '
                    f'{confidence_bar_html(rate)}</span>',
                    unsafe_allow_html=True,
                )
            with col_toggle:
                current = bool(profile["auto_action_eligible"])
                new_val = st.toggle("", value=current, key=f"auto_{email_key}")
                if new_val != current:
                    toggle_sender_auto_action(db, email_key, new_val)
                    st.toast(
                        f"Autopilot {'on' if new_val else 'off'} — {email_key}",
                        icon="⚡" if new_val else "⏸️",
                    )
    else:
        st.info("No sender profiles yet — they appear as you approve/reject items.")

    # ── Model health ────────────────────────────────────────────────
    _section("🤖", "Model health")
    model_meta = get_ml_model_metadata(db)

    if model_meta:
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Last trained",     format_unix_ts(model_meta.get("created_at")))
        with c2:
            acc = model_meta.get("accuracy")
            st.metric("Accuracy",         f"{acc:.1%}" if acc else "N/A")
        with c3:
            st.metric("Training samples", model_meta.get("training_samples", 0))
    else:
        st.info(
            "❓ No model trained yet.\n\n"
            "SSH into the machine and run:\n"
            "`python -m mailmind.scripts.train_ml_model`"
        )

    # ── Queue statistics ────────────────────────────────────────────
    _section("📬", "Queue statistics")
    stats = get_queue_stats(db, account=account)

    s1, s2, s3, s4, s5, s6 = st.columns(6)
    with s1: st.metric("Pending",    stats.get("pending",    0))
    with s2: st.metric("Approved",   stats.get("approved",   0))
    with s3: st.metric("Rejected",   stats.get("rejected",   0))
    with s4: st.metric("Executed",   stats.get("executed",   0))
    with s5: st.metric("Superseded", stats.get("superseded", 0))
    with s6: st.metric("Failed",     stats.get("failed",     0))

    rn = stats.get("reply_needed_pending", 0)
    if rn:
        st.warning(f"💬 {rn} pending item(s) with **Reply Needed** flag")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def _render_sidebar() -> Optional[str]:
    """Render mailbox switcher + watcher heartbeat; return selected account."""
    st.sidebar.markdown(
        '<div style="font-size:20px;font-weight:700;color:#E2E8F0;margin-bottom:4px;">'
        '📧 MailMind</div>'
        '<div style="font-size:11px;color:#64748B;margin-bottom:16px;">'
        'AI email assistant</div>',
        unsafe_allow_html=True,
    )

    accounts = get_accounts()
    account: Optional[str] = None
    if len(accounts) > 1:
        account = st.sidebar.radio("📮 Mailbox", accounts, index=0)
    elif len(accounts) == 1:
        account = accounts[0]
        st.sidebar.markdown(
            f'<div style="font-size:12px;color:#94A3B8;margin-bottom:8px;">'
            f'📮 {account}</div>',
            unsafe_allow_html=True,
        )

    # Heartbeat
    db  = get_db()
    raw = db.get_state("last_heartbeat_ts")
    hb  = get_heartbeat_status(int(raw) if raw else None)
    st.sidebar.markdown("---")

    dot_cls = {"fresh": "mm-status-fresh", "stale": "mm-status-stale", "never": "mm-status-never"}[hb["status"]]
    st.sidebar.markdown(
        f'<div style="font-size:12px;color:#94A3B8;display:flex;align-items:center;gap:4px;">'
        f'<span class="mm-status-dot {dot_cls}"></span>'
        f'Watcher: <b style="color:#E2E8F0;">{hb["human"]}</b></div>',
        unsafe_allow_html=True,
    )
    if hb["status"] == "stale":
        st.sidebar.warning("⚠️ Watch loop may be hung")
    elif hb["status"] == "never":
        st.sidebar.info("⏳ Start the watcher: `mailmind run --watch`")

    return account


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    inject_css()
    account = _render_sidebar()

    tab_now, tab_review, tab_automate = st.tabs(["📍 NOW", "📋 REVIEW", "⚙️ AUTOMATE"])

    with tab_now:
        render_now_tab(account)
    with tab_review:
        render_review_tab(account)
    with tab_automate:
        render_automate_tab(account)


if __name__ == "__main__":
    main()
