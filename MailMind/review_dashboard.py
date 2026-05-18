"""Streamlit review dashboard for MailMind.

Read‑only UI that displays predictions, actions, and sender reputations.
No body_text is ever exposed.
"""

from __future__ import annotations

import streamlit as st
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from MailMind.storage.database import Database
from MailMind.storage.queries import (
    get_recent_predictions,
    get_predictions_for_email,
    get_recent_actions,
    get_sender_reputations,
    get_summary_metrics,
)
    if senders:
        st.dataframe(senders, width='stretch')
    else:
        st.info("No sender reputation records found.")
