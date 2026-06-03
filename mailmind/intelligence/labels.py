"""Decide which Gmail labels count as user 'ground truth' for learning."""
from __future__ import annotations

import os
from typing import Dict, List, Optional

_SYSTEM = {
    "INBOX", "UNREAD", "IMPORTANT", "STARRED", "SENT", "DRAFT", "SPAM",
    "TRASH", "CHAT", "CATEGORY_PERSONAL", "CATEGORY_UPDATES",
    "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS",
}


def truth_label_policy() -> tuple[list[str], list[str]]:
    """(include_prefixes, exclude_prefixes) from env. Empty include = all custom."""
    inc = [p.strip() for p in os.environ.get("MAILMIND_TRUTH_LABELS_INCLUDE", "").split(",") if p.strip()]
    exc_raw = os.environ.get("MAILMIND_TRUTH_LABELS_EXCLUDE", "AI/,MailMind/")
    exc = [p.strip() for p in exc_raw.split(",") if p.strip()]
    return inc, exc


def is_truth_label(name: Optional[str], include: list[str], exclude: list[str]) -> bool:
    if not name:
        return False
    if name in _SYSTEM or name.startswith("CATEGORY_"):
        return False
    if any(name.startswith(p) for p in exclude):
        return False
    if include:
        return any(name.startswith(p) for p in include)
    return True


def resolve_truth_labels(
    label_ids: List[str], id_to_name: Dict[str, str],
    include: list[str], exclude: list[str],
) -> List[str]:
    """Map Gmail label IDs → names, keep only truth labels, sorted & de-duped."""
    out = set()
    for lid in label_ids or []:
        name = id_to_name.get(lid)
        if is_truth_label(name, include, exclude):
            out.add(name)
    return sorted(out)
