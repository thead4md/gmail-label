"""MailMind Dashboard — Streamlit web UI.

Three-tab interface:
  NOW      — high-priority / reply-needed items, single Approve action
  REVIEW   — all pending items with full reasoning and Approve/Reject/Edit
  AUTOMATE — activity digest, sender profiles, model health, queue stats

Design: dark-mode card layout (dashboard/theme.py), Altair charts for digest.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from mailmind.config import MailMindConfig
from mailmind.dashboard.helpers import (
    action_items_html,
    channel_chip_html,
    confidence_bar_html,
    confidence_sparkline_html,
    corrections_table_html,
    deadline_pill_html,
    email_card_html,
    email_preview_html,
    filter_now_items,
    format_unix_ts,
    get_confidence_badge,
    get_heartbeat_status,
    get_time_ago_str,
    history_badge_html,
    label_chip_html,
    parse_reason_json,
    reply_needed_pill_html,
    sender_avatar_html,
    sender_table_html,
    trust_badge_html,
)
from mailmind.dashboard import charts
from mailmind.dashboard.theme import (
    LABEL_COLORS,
    channel_color,
    inject_css,
    label_color,
    trust_color,
)
from mailmind.intelligence.feedback import (
    handle_approve, handle_correction, handle_reject,
    handle_know_sender, handle_mute_sender, handle_block_sender,
    handle_label_email,
)
from mailmind.processing.queue_manager import QueueManager
from mailmind.storage.database import Database
from mailmind.taxonomy import BASE_SCORES as LABEL_BASE_SCORES
from mailmind.storage.queries import (
    analytics_channel_distribution,
    analytics_channel_weekday,
    analytics_decision_times,
    analytics_label_distribution,
    analytics_top_senders,
    build_digest,
    get_executed_queue_enriched,
    get_gmail_labels,
    get_ml_model_metadata,
    get_new_senders,
    get_pending_queue_enriched,
    get_queue_stats,
    get_recent_corrections,
    get_recent_predictions_with_emails,
    get_sender_profiles,
    set_sender_label_rule,
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


# ---------------------------------------------------------------------------
# Cached read queries. TTLs chosen per volatility. Cleared by _invalidate()
# after any write (approve/reject/correct/toggle) so the UI never goes stale.
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def _c_pending(limit, account):
    return get_pending_queue_enriched(get_db(), limit=limit, account=account)

@st.cache_data(ttl=300)
def _c_recent_predictions(account):
    return get_recent_predictions_with_emails(get_db(), limit=200, account=account)

@st.cache_data(ttl=60)
def _c_queue_stats(account):
    return get_queue_stats(get_db(), account=account)

@st.cache_data(ttl=60)
def _c_digest(since_ts, account):
    return build_digest(get_db(), since_ts=since_ts, account=account)

@st.cache_data(ttl=600)
def _c_sender_profiles():
    return get_sender_profiles(get_db())

@st.cache_data(ttl=60)
def _c_new_senders(account):
    return get_new_senders(get_db(), account=account)

@st.cache_data(ttl=3600)
def _c_model_metadata():
    return get_ml_model_metadata(get_db())

@st.cache_data(ttl=300)
def _c_label_dist(since, account):
    return analytics_label_distribution(get_db(), since, account)

@st.cache_data(ttl=300)
def _c_gmail_labels(account):
    return get_gmail_labels(get_db(), account=account)

@st.cache_data(ttl=60)
def _c_executed(limit, account):
    return get_executed_queue_enriched(get_db(), limit=limit, account=account)

@st.cache_data(ttl=60)
def _c_corrections():
    return get_recent_corrections(get_db(), limit=50)

@st.cache_data(ttl=300)
def _c_channel_dist(since, account):
    return analytics_channel_distribution(get_db(), since, account)

@st.cache_data(ttl=300)
def _c_channel_weekday(since, account):
    return analytics_channel_weekday(get_db(), since, account)

@st.cache_data(ttl=300)
def _c_top_senders(since, account):
    return analytics_top_senders(get_db(), since, account=account)

@st.cache_data(ttl=300)
def _c_decision_times(since, account):
    return analytics_decision_times(get_db(), since, account)

@st.cache_data(ttl=3600)
def _c_daily_brief(account):
    from mailmind.intelligence.brief import build_daily_brief
    from mailmind.llm.deepseek import DeepSeekClient
    from mailmind.config import MailMindConfig

    db = get_db()
    config = MailMindConfig.from_env()
    llm_client = None
    if config.llm_enabled and config.deepseek_api_key:
        try:
            llm_client = DeepSeekClient(config)
        except Exception:
            pass
    return build_daily_brief(db, account=account, llm_client=llm_client)

def _invalidate() -> None:
    """Clear queue-affected caches after approve/reject/correct/label-weight writes."""
    _c_pending.clear()
    _c_queue_stats.clear()
    _c_executed.clear()
    _c_digest.clear()
    _c_new_senders.clear()
    _c_corrections.clear()


def _invalidate_senders() -> None:
    """Clear sender-profile caches after know/mute/block/autopilot-toggle writes."""
    _c_sender_profiles.clear()
    _c_new_senders.clear()


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
    all_items = _c_pending(200, account)
    # Remove items acted on earlier this session before the cache refreshes.
    _dismissed = st.session_state.get("dismissed_ids", set())
    all_items  = [i for i in all_items if i.get("id") not in _dismissed]
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

    # Display daily brief (if available)
    daily_brief = _c_daily_brief(account)
    if daily_brief:
        with st.expander("📋 Today's brief", expanded=False):
            st.markdown(daily_brief)
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

        snippet = item.get("snippet") or ""
        _prev = email_preview_html(snippet)
        if _prev:
            st.markdown(_prev, unsafe_allow_html=True)

        # Unsubscribe link for newsletters
        unsub_url = reason.get("unsubscribe_url")
        channel = item.get("channel")
        if unsub_url and channel == "newsletter":
            escaped_url = html.escape(unsub_url)
            st.markdown(
                f'<a href="{escaped_url}" target="_blank" style="'
                f'display:inline-block;font-size:12px;color:#5B8AF0;'
                f'text-decoration:none;padding:4px 8px;'
                f'border:1px solid #2D3656;border-radius:3px;">'
                f'Unsubscribe ↗</a>',
                unsafe_allow_html=True,
            )

        # Action items and deadlines
        ai_html = action_items_html(reason.get("action_items"))
        dl_html = deadline_pill_html(reason.get("deadlines"))
        if ai_html or dl_html:
            st.markdown(dl_html + ai_html, unsafe_allow_html=True)

        # Quick-action row: [label dropdown] [Approve] [Reject]
        gmail_labels = _c_gmail_labels(account) or list(LABEL_COLORS.keys())
        predicted_label = label or (gmail_labels[0] if gmail_labels else "WORK")
        default_idx = gmail_labels.index(predicted_label) if predicted_label in gmail_labels else 0
        col_sel, col_approve, col_reject, col_spacer = st.columns([2, 1, 1, 3])
        with col_sel:
            chosen_label = st.selectbox(
                "Label", options=gmail_labels, index=default_idx,
                key=f"now_label_{idx}_{item_id}", label_visibility="collapsed",
            )
        with col_approve:
            st.markdown('<div class="mm-btn-approve">', unsafe_allow_html=True)
            if st.button("✅ Approve", key=f"now_approve_{idx}_{item_id}"):
                if chosen_label != predicted_label:
                    handle_correction(db, item_id, corrected_label=chosen_label)
                acted = handle_approve(db, item_id, executor=get_action_executor())
                if acted:
                    st.session_state.setdefault("dismissed_ids", set()).add(item_id)
                    st.toast(f"✅ {subject[:50]}", icon="✅")
                    _invalidate()
                    st.rerun()
                else:
                    st.warning("Already processed or no longer exists.")
            st.markdown("</div>", unsafe_allow_html=True)
        with col_reject:
            st.markdown('<div class="mm-btn-reject">', unsafe_allow_html=True)
            if st.button("❌ Reject", key=f"now_reject_{idx}_{item_id}"):
                acted = handle_reject(db, item_id)
                if acted:
                    st.session_state.setdefault("dismissed_ids", set()).add(item_id)
                    st.toast(f"❌ {subject[:50]}", icon="❌")
                    _invalidate()
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

    spark = confidence_sparkline_html(reason)
    if spark:
        rows.append(("Confidence trend", spark))

    tier = item.get("trust_tier") or reason.get("trust_tier")
    if tier:
        rows.append(("Sender trust", trust_badge_html(tier)))

    ch = item.get("channel")
    if ch:
        rows.append(("Channel", channel_chip_html(ch)))

    if reason.get("rule_matches"):
        rules_html = " ".join(
            f'<code style="font-size:11px;background:#1C2237;padding:1px 5px;'
            f'border-radius:3px;">{html.escape(str(r))}</code>'
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
        rows.append(("Thread", f'<em style="color:#94A3B8;">{html.escape(reason["thread_summary"][:150])}</em>'))

    past = reason.get("similar_past_actions") or []
    if past:
        actions_html = " ".join(
            f'<code style="font-size:11px;background:#1C2237;padding:1px 5px;border-radius:3px;">'
            f'{html.escape(str(e.get("action", str(e))))}</code>'
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

    # ── New senders ──────────────────────────────────────────────────
    _dismissed_senders = st.session_state.get("dismissed_senders", set())
    new_senders = [s for s in _c_new_senders(account)
                   if s["sender"] not in _dismissed_senders]
    if new_senders:
        _section("🆕", f"New senders — {len(new_senders)}")
        for i, s in enumerate(new_senders):
            sender = s["sender"]
            c_name, c_know, c_mute, c_block = st.columns([3, 1, 1, 1])
            with c_name:
                st.markdown(
                    f'{sender_avatar_html(sender)}&nbsp;'
                    f'<span style="font-size:13px;">{html.escape(sender)}</span> '
                    f'<span style="font-size:11px;color:#94A3B8;">'
                    f'{s["email_count"]} emails</span>',
                    unsafe_allow_html=True,
                )
            with c_know:
                if st.button("✅ Know", key=f"know_{i}_{sender}"):
                    handle_know_sender(db, sender)
                    st.session_state.setdefault("dismissed_senders", set()).add(sender)
                    st.toast("Trusted", icon="✅"); _invalidate_senders(); st.rerun()
            with c_mute:
                if st.button("🔇 Mute", key=f"mute_{i}_{sender}"):
                    handle_mute_sender(db, sender)
                    st.session_state.setdefault("dismissed_senders", set()).add(sender)
                    st.toast("Muted", icon="🔇"); _invalidate_senders(); st.rerun()
            with c_block:
                if st.button("🚫 Block", key=f"block_{i}_{sender}"):
                    handle_block_sender(db, sender)
                    st.session_state.setdefault("dismissed_senders", set()).add(sender)
                    st.toast("Blocked", icon="🚫"); _invalidate_senders(); st.rerun()

    # ── Recent predictions ───────────────────────────────────────────
    _section("📊", "Recent predictions")
    preds = _c_recent_predictions(account)
    if preds:
        df = pd.DataFrame(preds)
        df["date"] = df["date"].apply(lambda ts: format_unix_ts(ts) if ts else "")
        df = df.drop(columns=["preview", "email_gmail_id"], errors="ignore")
        st.dataframe(df, use_container_width=True, height=220)
    else:
        st.info("No predictions yet — run the pipeline to classify emails.")

    # ── Pending approval ─────────────────────────────────────────────
    _dismissed = st.session_state.get("dismissed_ids", set())
    items = [i for i in _c_pending(None, account) if i.get("id") not in _dismissed]
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

            snippet = item.get("snippet") or ""
            _prev = email_preview_html(snippet)
            if _prev:
                st.markdown(
                    '<div class="mm-section-header" style="margin-top:12px;">📄 Preview</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(_prev, unsafe_allow_html=True)

            # Unsubscribe link for newsletters
            unsub_url = reason.get("unsubscribe_url")
            ch = item.get("channel")
            if unsub_url and ch == "newsletter":
                escaped_url = html.escape(unsub_url)
                st.markdown(
                    f'<a href="{escaped_url}" target="_blank" style="'
                    f'display:inline-block;font-size:12px;color:#5B8AF0;'
                    f'text-decoration:none;padding:4px 8px;'
                    f'border:1px solid #2D3656;border-radius:3px;margin-top:8px;">'
                    f'Unsubscribe ↗</a>',
                    unsafe_allow_html=True,
                )

            # Action buttons
            st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
            col_approve, col_reject, col_edit = st.columns(3)

            with col_approve:
                st.markdown('<div class="mm-btn-approve">', unsafe_allow_html=True)
                if st.button("✅ Approve", key=f"review_approve_{idx}_{item_id}"):
                    acted = handle_approve(db, item_id, executor=get_action_executor())
                    if acted:
                        st.session_state.setdefault("dismissed_ids", set()).add(item_id)
                        st.toast("✅ Approved", icon="✅")
                        _invalidate()
                        st.rerun()
                    else:
                        st.warning("Already processed or no longer exists.")
                st.markdown("</div>", unsafe_allow_html=True)

            with col_reject:
                st.markdown('<div class="mm-btn-reject">', unsafe_allow_html=True)
                if st.button("❌ Reject", key=f"review_reject_{idx}_{item_id}"):
                    acted = handle_reject(db, item_id)
                    if acted:
                        st.session_state.setdefault("dismissed_ids", set()).add(item_id)
                        st.toast("❌ Rejected", icon="❌")
                        _invalidate()
                        st.rerun()
                    else:
                        st.warning("Already processed or no longer exists.")
                st.markdown("</div>", unsafe_allow_html=True)

            with col_edit:
                if st.button("✏️ Edit label", key=f"review_edit_{idx}_{item_id}"):
                    st.session_state[f"edit_review_{item_id}"] = True

            if st.session_state.get(f"edit_review_{item_id}"):
                col_sel, col_scope, col_save = st.columns(3)
                gmail_labels = _c_gmail_labels(account) or list(LABEL_COLORS.keys())
                with col_sel:
                    new_label = st.selectbox(
                        "New label",
                        options=gmail_labels,
                        key=f"review_label_select_{item_id}",
                    )
                with col_scope:
                    scope = st.radio(
                        "Apply to",
                        options=["email", "thread", "sender"],
                        horizontal=True,
                        key=f"review_label_scope_{item_id}",
                    )
                with col_save:
                    if st.button("Save", key=f"review_save_label_{item_id}"):
                        if scope in ("thread", "sender"):
                            acted = handle_label_email(db, item_id, new_label, scope, executor=get_action_executor(), account=account)
                        else:
                            acted = handle_correction(db, item_id, corrected_label=new_label)
                        if acted:
                            st.toast(f"✏️ → {new_label} ({scope})", icon="✏️")
                            st.session_state.pop(f"edit_review_{item_id}", None)
                            _invalidate()
                            st.rerun()
                        else:
                            st.warning("Item no longer exists.")


# ---------------------------------------------------------------------------
# Tab: HISTORY
# ---------------------------------------------------------------------------
def render_history_tab(account: Optional[str] = None) -> None:
    import time as _time
    db = get_db()
    _section("📋", "Recent activity")
    col_win, _ = st.columns([1, 3])
    with col_win:
        history_days = st.slider("Window (days)", 1, 30, 7, key="history_days")
    cutoff_ts = int(_time.time()) - history_days * 86400
    all_executed = _c_executed(100, account)
    items = [
        it for it in all_executed
        if (it.get("executed_at") or it.get("reviewed_at") or it.get("created_at") or 0)
        >= cutoff_ts
    ]
    if not items:
        st.markdown(
            '<div class="mm-empty">'
            '<div class="mm-empty-icon">📭</div>'
            '<div class="mm-empty-text">No activity in this window</div>'
            '<div class="mm-empty-sub">Widen the slider or run the pipeline</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        for idx, item in enumerate(items):
            item_id  = item.get("id")
            subject  = (item.get("subject") or "[No Subject]")[:60]
            sender   = (item.get("sender")  or "Unknown")[:30]
            label    = item.get("primary_label")
            conf     = item.get("confidence") or 0.0
            was_auto = item.get("was_auto", False)
            status   = item.get("status", "executed")
            actioned_ts = (
                item.get("executed_at") or item.get("reviewed_at") or item.get("created_at")
            )
            time_ago = get_time_ago_str(actioned_ts)
            status_icon = {"executed": "✅", "approved": "👍", "execute_failed": "⚠️"}.get(status, "•")
            with st.expander(f"{status_icon} {sender} — {subject}", expanded=False):
                st.markdown(
                    f'{label_chip_html(label)} {history_badge_html(was_auto)} '
                    f'<span style="font-size:11px;color:#94A3B8;">{time_ago}</span>',
                    unsafe_allow_html=True,
                )
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.markdown(f"**Sender:** {item.get('sender', 'Unknown')}")
                    st.markdown(f"**Action:** `{item.get('action', 'N/A')}`")
                    st.markdown(f"**Status:** `{status}`")
                with col2:
                    st.markdown(
                        f"**Label:** {label_chip_html(label)}&nbsp;&nbsp;{confidence_bar_html(conf)}",
                        unsafe_allow_html=True,
                    )
                    ch = item.get("channel")
                    if ch:
                        st.markdown(channel_chip_html(ch), unsafe_allow_html=True)
                with col3:
                    st.markdown(f"**Actioned:** {format_unix_ts(actioned_ts)}")
                    st.markdown(f"**Mode:** {'🤖 Auto-pilot' if was_auto else '👤 Manual'}")
                st.markdown(
                    '<div class="mm-section-header" style="margin-top:12px;">🤔 Why this?</div>',
                    unsafe_allow_html=True,
                )
                reason = parse_reason_json(item.get("reason_json"))
                _render_reason_panel(reason, item)
                snippet = item.get("snippet") or ""
                _prev = email_preview_html(snippet)
                if _prev:
                    st.markdown(
                        '<div class="mm-section-header" style="margin-top:12px;">📄 Preview</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(_prev, unsafe_allow_html=True)
                st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
                if st.button("✏️ Correct label", key=f"hist_edit_{idx}_{item_id}"):
                    st.session_state[f"edit_history_{item_id}"] = True
                if st.session_state.get(f"edit_history_{item_id}"):
                    gmail_labels = _c_gmail_labels(account) or list(LABEL_COLORS.keys())
                    predicted_label = label or (gmail_labels[0] if gmail_labels else "WORK")
                    default_idx = gmail_labels.index(predicted_label) if predicted_label in gmail_labels else 0
                    col_sel, col_save, col_cancel = st.columns([2, 1, 1])
                    with col_sel:
                        new_label = st.selectbox(
                            "Correct label", options=gmail_labels, index=default_idx,
                            key=f"hist_label_select_{item_id}",
                        )
                    with col_save:
                        if st.button("Save", key=f"hist_save_label_{item_id}"):
                            acted = handle_correction(db, item_id, corrected_label=new_label)
                            if acted:
                                st.toast(f"✏️ Corrected → {new_label}", icon="✏️")
                                st.session_state.pop(f"edit_history_{item_id}", None)
                                _invalidate()
                                st.rerun()
                            else:
                                st.warning("Item no longer found in queue.")
                    with col_cancel:
                        if st.button("Cancel", key=f"hist_cancel_{item_id}"):
                            st.session_state.pop(f"edit_history_{item_id}", None)
                            st.rerun()
    _section("✏️", "Correction history")
    corrections = _c_corrections()
    if corrections:
        st.markdown(corrections_table_html(corrections), unsafe_allow_html=True)
    else:
        st.info("No corrections yet — correct a label above to start training the system.")


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
    digest   = _c_digest(since_ts, account)

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
    profiles = _c_sender_profiles()

    if profiles:
        st.markdown(sender_table_html(profiles), unsafe_allow_html=True)

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
                    f'<span style="font-size:13px;">{html.escape(email_key)}</span>',
                    unsafe_allow_html=True,
                )
            with col_stats:
                appr  = profile.get("total_approved", 0)
                rej   = profile.get("total_rejected", 0)
                rate  = profile.get("approval_rate", 0.0)
                vol   = profile.get("email_count", 0)
                if (appr + rej) > 0:
                    stats_html = (
                        f'✅ {appr} approved &nbsp; ❌ {rej} rejected &nbsp; '
                        f'{confidence_bar_html(rate)}'
                    )
                else:
                    stats_html = f'📧 {vol} emails'
                st.markdown(
                    f'<span style="font-size:12px;color:#94A3B8;">{stats_html}</span>',
                    unsafe_allow_html=True,
                )
            with col_toggle:
                current = bool(profile["auto_action_eligible"])
                new_val = st.toggle("", value=current, key=f"auto_{email_key}")
                if new_val != current:
                    toggle_sender_auto_action(db, email_key, new_val)
                    _invalidate_senders()
                    st.toast(
                        f"Autopilot {'on' if new_val else 'off'} — {email_key}",
                        icon="⚡" if new_val else "⏸️",
                    )
    else:
        st.info("No emails in the database yet.")

    # ── Label priority weights ──────────────────────────────────────
    _section("⚖️", "Label priority weights")

    label_priorities = db.get_label_priorities()
    st.markdown(
        "<p style='font-size:12px;color:#94A3B8;margin-bottom:16px;'>"
        "Set weights (−20 to +30) to boost or suppress specific labels in scoring. "
        "Higher weights increase priority scores.</p>",
        unsafe_allow_html=True,
    )

    # Predefined labels from scorer
    all_labels = list(LABEL_BASE_SCORES.keys())

    for label in all_labels:
        col_label, col_slider, col_reset = st.columns([1, 3, 1])
        with col_label:
            st.markdown(
                f'<span style="font-size:13px;font-weight:500;">{label}</span>',
                unsafe_allow_html=True,
            )
        with col_slider:
            current_weight = label_priorities.get(label, 0)
            new_weight = st.slider(
                "weight",
                min_value=-20,
                max_value=30,
                value=current_weight,
                step=1,
                key=f"label_priority_{label}",
                label_visibility="collapsed",
            )
            if new_weight != current_weight:
                db.set_label_priority(label, new_weight)
                _invalidate()
                st.toast(f"Updated {label} weight to {new_weight}", icon="⚖️")
        with col_reset:
            if label_priorities.get(label) is not None:
                if st.button("Reset", key=f"reset_priority_{label}", use_container_width=True):
                    # Remove by setting to 0
                    db.set_label_priority(label, 0)
                    _invalidate()
                    st.toast(f"Reset {label} weight", icon="↺")

    # ── Create rules from natural language ──────────────────────────
    _section("✨", "Create rule from description")

    from mailmind.intelligence.nl_rules import parse_rule_nl
    from mailmind.config import MailMindConfig
    from mailmind.llm.deepseek import DeepSeekClient

    # from_env() loads the API key (.env / secrets); the bare constructor does
    # not, which left the NL-rule parser's LLM call keyless and always failing.
    config = MailMindConfig.from_env()
    rule_text = st.text_input(
        "Describe a rule (e.g., 'label emails from billing@acme.com as FINANCE', "
        "or 'label emails from oe-l@cserkesz.hu about events as CALENDAR')",
        placeholder="e.g., label anything from newsletter@example.com as NEWSLETTER",
        key="nl_rule_input",
    )

    if st.button("Create rule", key="create_rule_button", use_container_width=True):
        if rule_text.strip():
            try:
                client = DeepSeekClient(config)
                result = parse_rule_nl(rule_text.strip(), client)

                if result.get("error"):
                    st.error(f"❌ {result['error']}")
                elif result.get("unsupported"):
                    st.warning(
                        f"⚠️ {result['error']}"
                    )
                else:
                    sender = result.get("sender_email")
                    label = result.get("label")
                    match_pattern = result.get("match_pattern")
                    if sender and label:
                        from mailmind.storage.queries import set_sender_label_rule
                        set_sender_label_rule(
                            db, sender, label, account=account,
                            match_pattern=match_pattern,
                        )
                        _invalidate()
                        scope_note = (
                            f" when subject matches /{match_pattern}/"
                            if match_pattern else " (all messages)"
                        )
                        st.toast(
                            f"✅ Rule created: {sender} → {label}{scope_note}",
                            icon="✨",
                        )
                    else:
                        st.error("❌ Could not extract sender and label from your description.")
            except Exception as e:
                st.error(f"❌ Error creating rule: {str(e)}")
        else:
            st.warning("Please enter a rule description.")

    # ── Model health ────────────────────────────────────────────────
    _section("🤖", "Model health")
    model_meta = _c_model_metadata()

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

    # ── Bulk newsletter unsubscribe ─────────────────────────────────
    _section("📰", "Newsletters — unsubscribe")

    newsletters_with_unsub = db.execute_sql(
        """
        SELECT DISTINCT e.sender, e.unsubscribe_url, COUNT(*) as email_count
        FROM emails e
        WHERE e.unsubscribe_url IS NOT NULL
          AND e.sender IS NOT NULL
        GROUP BY e.sender, e.unsubscribe_url
        ORDER BY email_count DESC
        LIMIT 20
        """
    ).fetchall()

    if newsletters_with_unsub:
        st.markdown(
            "<p style='font-size:12px;color:#94A3B8;margin-bottom:12px;'>"
            "Newsletters and bulk senders with unsubscribe links (one row per sender).</p>",
            unsafe_allow_html=True,
        )
        for row in newsletters_with_unsub:
            sender = row["sender"] or "Unknown"
            unsub_url = row["unsubscribe_url"]
            count = row["email_count"]
            escaped_url = html.escape(unsub_url)
            col_sender, col_count, col_unsub = st.columns([2, 1, 1])
            with col_sender:
                st.markdown(
                    f'<span style="font-size:12px;">{html.escape(sender)}</span>',
                    unsafe_allow_html=True,
                )
            with col_count:
                st.markdown(
                    f'<span style="font-size:11px;color:#94A3B8;">{count} emails</span>',
                    unsafe_allow_html=True,
                )
            with col_unsub:
                st.markdown(
                    f'<a href="{escaped_url}" target="_blank" style="'
                    f'display:inline-block;font-size:11px;color:#5B8AF0;'
                    f'text-decoration:none;">'
                    f'Unsubscribe ↗</a>',
                    unsafe_allow_html=True,
                )
    else:
        st.info("No newsletters with unsubscribe links yet.")

    # ── Queue statistics ────────────────────────────────────────────
    _section("📬", "Queue statistics")
    stats = _c_queue_stats(account)

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


def _get_theme() -> str:
    """Return the current theme preference ('dark', 'light', 'system')."""
    return st.session_state.get("_theme", "dark")


def _render_sidebar() -> Optional[str]:
    """Render mailbox switcher, heartbeat, theme picker; return selected account."""
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

    # Theme picker
    st.sidebar.markdown("---")
    _THEME_OPTIONS = {"🌙 Dark": "dark", "☀️ Light": "light", "💻 System": "system"}
    current = _get_theme()
    current_label = next(k for k, v in _THEME_OPTIONS.items() if v == current)
    chosen_label = st.sidebar.radio(
        "Theme", list(_THEME_OPTIONS.keys()),
        index=list(_THEME_OPTIONS.keys()).index(current_label),
        label_visibility="collapsed",
    )
    chosen = _THEME_OPTIONS[chosen_label]
    if chosen != current:
        st.session_state["_theme"] = chosen
        st.rerun()

    return account


# ---------------------------------------------------------------------------
# Tab 3: INSIGHTS
# ---------------------------------------------------------------------------


def render_insights_tab(account: Optional[str] = None) -> None:
    import time as _time
    db = get_db()

    days = st.slider("Window (days)", 1, 90, 30, key="insights_days")
    since = int(_time.time()) - days * 86400

    # NOTE: use explicit if/else (NOT a ternary expression-statement). A bare
    # `st.altair_chart(...) if c else st.info(...)` is an expression statement;
    # Streamlit "magic" wraps it in st.write(), and since st.altair_chart returns
    # a DeltaGenerator, magic then dumps the DeltaGenerator help table to the page.
    def _chart_or_info(chart, empty_msg: str) -> None:
        if chart is not None:
            st.altair_chart(chart, use_container_width=True)
        else:
            st.info(empty_msg)

    _section("📊", "Label distribution")
    _chart_or_info(
        charts.label_distribution_chart(_c_label_dist(since, account)),
        "No data yet.")

    _section("📨", "Channel volume")
    _chart_or_info(
        charts.channel_distribution_chart(_c_channel_dist(since, account)),
        "No data yet.")

    _section("🗓️", "Channel × weekday")
    _chart_or_info(
        charts.channel_weekday_heatmap(_c_channel_weekday(since, account)),
        "No data yet.")

    _section("👤", "Top senders")
    _chart_or_info(
        charts.top_senders_chart(_c_top_senders(since, account)),
        "No data yet.")

    _section("⏱️", "Time to decision")
    _chart_or_info(
        charts.decision_time_chart(_c_decision_times(since, account)),
        "No reviewed items yet.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


_AUTH_COOKIE   = "mm_auth"
_AUTH_DAYS     = 30


def _make_auth_token(secret: str) -> str:
    import hmac, hashlib, time
    expiry = int(time.time()) + _AUTH_DAYS * 86400
    sig = hmac.new(secret.encode(), str(expiry).encode(), hashlib.sha256).hexdigest()
    return f"{expiry}:{sig}"


def _valid_auth_token(token: str, secret: str) -> bool:
    import hmac, hashlib, time
    try:
        expiry_str, sig = token.split(":", 1)
        if int(expiry_str) < int(time.time()):
            return False
        expected = hmac.new(secret.encode(), expiry_str.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


_AUTH_MAX_FAILURES = 5
_AUTH_LOCKOUT_SECONDS = 300
_AUTH_STATE_KEY = "dashboard_auth_state"


def _auth_secret(password: str) -> str:
    """HMAC signing key for the auth cookie. Prefer a dedicated DASHBOARD_SECRET so a
    stolen cookie can't be brute-forced against a weak password; fall back to the password
    when unset so existing deployments keep working."""
    import os
    return os.environ.get("DASHBOARD_SECRET", "").strip() or password


def _auth_lockout_remaining() -> int:
    import json, time
    raw = get_db().get_state(_AUTH_STATE_KEY)
    if not raw:
        return 0
    try:
        data = json.loads(raw)
    except Exception:
        return 0
    return max(0, int(data.get("locked_until", 0)) - int(time.time()))


def _record_auth_failure() -> None:
    import json, time
    raw = get_db().get_state(_AUTH_STATE_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    failures = int(data.get("failures", 0)) + 1
    locked_until = 0
    if failures >= _AUTH_MAX_FAILURES:
        locked_until = int(time.time()) + _AUTH_LOCKOUT_SECONDS
        failures = 0
    get_db().set_state(_AUTH_STATE_KEY, json.dumps({"failures": failures, "locked_until": locked_until}))


def _reset_auth_failures() -> None:
    import json
    get_db().set_state(_AUTH_STATE_KEY, json.dumps({"failures": 0, "locked_until": 0}))


def _check_password() -> bool:
    """Return True if the user is authenticated (or no password is configured).

    Uses a 30-day HMAC-signed cookie so the user doesn't need to re-enter the
    password on every browser session. Session state acts as the fast path
    (no cookie round-trip within the same tab).
    """
    import os
    from streamlit_cookies_controller import CookieController

    required = os.environ.get("DASHBOARD_PASSWORD", "").strip()
    if not required:
        return True

    # Fast path: already authenticated this session.
    if st.session_state.get("_authenticated"):
        return True

    # Persistent path: check the cookie (may be None on the very first render
    # while the cookie component hydrates — handled below).
    controller = CookieController()
    token = controller.get(_AUTH_COOKIE)

    if token and _valid_auth_token(token, _auth_secret(required)):
        st.session_state["_authenticated"] = True
        return True

    # If the token was set but is invalid/expired, clear it.
    if token:
        controller.remove(_AUTH_COOKIE)

    inject_css()
    st.markdown(
        '<div style="max-width:360px;margin:80px auto 0;">'
        '<div style="font-size:24px;font-weight:700;color:#E2E8F0;margin-bottom:4px;">📧 MailMind</div>'
        '<div style="font-size:12px;color:#64748B;margin-bottom:28px;">Enter the dashboard password to continue.</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    col, _ = st.columns([1, 2])
    with col:
        pwd = st.text_input("Password", type="password", label_visibility="collapsed",
                            placeholder="Password")
        locked = _auth_lockout_remaining()
        if locked > 0:
            st.error(f"Too many attempts. Try again in {locked}s.")
        elif st.button("Unlock", use_container_width=True):
            import hmac
            if hmac.compare_digest(pwd, required):
                controller.set(_AUTH_COOKIE, _make_auth_token(_auth_secret(required)),
                               max_age=_AUTH_DAYS * 86400)
                st.session_state["_authenticated"] = True
                _reset_auth_failures()
                st.rerun()
            else:
                _record_auth_failure()
                st.error("Incorrect password.")
    return False


def main() -> None:
    if not _check_password():
        return

    inject_css(_get_theme())
    account = _render_sidebar()

    tab_now, tab_review, tab_history, tab_insights, tab_automate = st.tabs(
        ["📍 NOW", "📋 REVIEW", "🕐 HISTORY", "📈 INSIGHTS", "⚙️ AUTOMATE"])

    with tab_now:
        render_now_tab(account)
    with tab_review:
        render_review_tab(account)
    with tab_history:
        render_history_tab(account)
    with tab_insights:
        render_insights_tab(account)
    with tab_automate:
        render_automate_tab(account)


if __name__ == "__main__":
    main()
