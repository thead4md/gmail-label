from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends

from mailmind.api.auth import require_auth
from mailmind.api.deps import get_db
from mailmind.storage.queries import (
    analytics_autopilot_precision,
    analytics_channel_distribution,
    analytics_channel_weekday,
    analytics_decision_times,
    analytics_label_distribution,
    analytics_llm_cost,
    analytics_tier_quality,
    analytics_top_senders,
)

router = APIRouter(prefix="/api/insights", tags=["insights"], dependencies=[Depends(require_auth)])


@router.get("")
def insights(account: Optional[str] = None, days: int = 30) -> dict:
    db = get_db()
    since = int(time.time()) - days * 86400

    tiers = []
    try:
        for row in analytics_tier_quality(db, since, account) or []:
            total = int(row.get("total") or 0)
            corr = int(row.get("corrections") or 0)
            rate = row.get("correction_rate")
            rate = float(rate) if isinstance(rate, (int, float)) else (corr / total if total else 0.0)
            tiers.append({"source": str(row.get("source") or "rules"), "total": total,
                          "corrections": corr, "correction_rate": rate})
    except Exception:
        tiers = []

    try:
        auto = analytics_autopilot_precision(db, since, account) or {}
        precision = auto.get("precision")
        autopilot = {
            "auto_executed": int(auto.get("auto_executed") or 0),
            "later_corrected": int(auto.get("later_corrected") or 0),
            "precision": float(precision) if isinstance(precision, (int, float)) else None,
        }
    except Exception:
        autopilot = {"auto_executed": 0, "later_corrected": 0, "precision": None}

    try:
        cost_raw = analytics_llm_cost(db, since) or {}
        by_kind = [
            {"model": str(r.get("model") or ""), "kind": str(r.get("kind") or ""),
             "calls": int(r.get("calls") or 0), "cost_usd": float(r.get("cost_usd") or 0.0)}
            for r in cost_raw.get("by_kind") or []
        ]
        llm_cost = {
            "calls": int(cost_raw.get("calls") or 0),
            "cost_usd": float(cost_raw.get("cost_usd") or 0.0),
            "tokens": int(cost_raw.get("tokens") or 0),
            "avg_latency_ms": int(cost_raw.get("avg_latency_ms") or 0),
            "by_kind": by_kind,
        }
    except Exception:
        llm_cost = {"calls": 0, "cost_usd": 0.0, "tokens": 0, "avg_latency_ms": 0, "by_kind": []}

    return {
        "label_distribution": analytics_label_distribution(db, since, account),
        "channel_distribution": analytics_channel_distribution(db, since, account),
        "channel_weekday": analytics_channel_weekday(db, since, account),
        "top_senders": analytics_top_senders(db, since, account=account),
        "decision_times": analytics_decision_times(db, since, account),
        "tier_quality": tiers,
        "autopilot_precision": autopilot,
        "llm_cost": llm_cost,
    }
