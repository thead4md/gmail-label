"""Periodic label discovery.

Clusters the recent email content window to surface recurring themes that the
user's current taxonomy does NOT capture, and proposes them as new labels.

Strategy:
  1. Pull recent emails (window_days) whose current label is generic
     (NEWSLETTER/NOTIFICATION/MASS_EMAIL/OTHER) or low-confidence — i.e. exactly
     where the taxonomy has gaps.
  2. TF-IDF + KMeans cluster the content-only text (no sender identity).
  3. For each sufficiently large, coherent cluster, build a candidate:
     top terms, example message ids, a cohesion score.
  4. Drop candidates whose theme is already an in-use label.
  5. Name each candidate — via the LLM when available, else from top terms.
  6. Persist as 'pending' label_suggestions (idempotent on the label name).

Review-only: nothing is auto-applied. The user accepts/dismisses in the
dashboard. Designed to run on a ~1-2 month cadence from the watch loop.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional, List, Dict, Any

from ..ml.features import build_content_text
from ..storage.database import Database
from ..storage.queries import (
    get_in_use_labels,
    save_label_suggestion,
    get_label_suggestions,
)

LOG = logging.getLogger(__name__)

# Labels considered "generic" — emails stuck on these are taxonomy gaps worth mining.
GENERIC_LABELS = {"NEWSLETTER", "NOTIFICATION", "MASS_EMAIL", "OTHER", "UNKNOWN", ""}

_TOKEN_RE = re.compile(r"[a-z][a-z0-9]{2,}")

# This mailbox is largely Hungarian; sklearn only ships English stopwords, so
# without these the clusters get named by Hungarian filler/greeting words
# ("hogy", "kedves", "sziasztok"…) instead of by topic.
_HUNGARIAN_STOPWORDS = {
    "hogy", "nem", "egy", "isten", "szia", "sziasztok", "kedves", "tisztelt",
    "üdv", "udv", "udvozlettel", "üdvözlettel", "köszönöm", "koszonom", "koszi",
    "köszi", "ezt", "azt", "ami", "mint", "csak", "még", "meg", "már", "mar",
    "vagy", "lesz", "volt", "lenne", "kell", "lehet", "lehetne", "ezek", "azok",
    "ehhez", "ahhoz", "minden", "illetve", "valamint", "tehát", "tehat", "ill",
    "stb", "pedig", "azonban", "amely", "amelyek", "akik", "aki", "ezzel",
    "ezért", "ezert", "miatt", "után", "utan", "előtt", "elott", "során", "soran",
    "felé", "fele", "részére", "reszere", "számára", "szamara", "esetén", "eseten",
    "com", "www", "http", "https", "gmail", "email", "mail", "from", "subject",
}


def _stop_words() -> list:
    """English + Hungarian stopwords as a list for TfidfVectorizer."""
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
    return sorted(set(ENGLISH_STOP_WORDS) | _HUNGARIAN_STOPWORDS)


def _chat_complete(llm_client, system: str, user: str, max_tokens: int = 60) -> str:
    """Single chat completion that works for both LLM client shapes.

    - DeepSeekClient exposes `.client` (an OpenAI-compatible client) + `.model`.
    - OpenAIAdapter wraps an LLMClassifier (`.classifier.api_key` / `.model`) and
      constructs the OpenAI client on demand, with no persistent `.client`.
    """
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    client = getattr(llm_client, "client", None)
    model = getattr(llm_client, "model", None)
    if client is not None and model:
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.2, max_tokens=max_tokens)
        return resp.choices[0].message.content or ""
    inner = getattr(llm_client, "classifier", None)
    if inner is not None and getattr(inner, "api_key", None):
        import openai
        oc = openai.OpenAI(api_key=inner.api_key)
        resp = oc.chat.completions.create(
            model=getattr(inner, "model", "gpt-4o-mini"),
            messages=messages, temperature=0.2, max_tokens=max_tokens)
        return resp.choices[0].message.content or ""
    raise RuntimeError("no usable LLM chat interface on client")


def _fetch_window(db: Database, window_days: int, account: Optional[str]) -> List[Dict[str, Any]]:
    """Recent emails on a generic/empty label, joined to their content."""
    since = int(time.time()) - window_days * 86400
    acct_clause = "AND e.account = ?" if account else ""
    params: tuple = (since, *( (account,) if account else () ))
    rows = db.execute_sql(
        f"""
        SELECT e.gmail_id, e.subject, e.snippet, e.body_text,
               p.primary_label AS label, p.confidence AS conf
        FROM emails e
        LEFT JOIN predictions p ON p.email_gmail_id = e.gmail_id
        WHERE e.date_ts >= ?
          {acct_clause}
        """,
        params,
    ).fetchall()
    out = []
    for r in rows:
        label = (r["label"] or "").upper()
        conf = r["conf"]
        # Mine generic-labelled OR low-confidence mail (the taxonomy gaps).
        if label in GENERIC_LABELS or (conf is not None and conf < 0.5):
            out.append(dict(r))
    return out


def _keyword_name(terms: List[str]) -> str:
    """Fallback label name from the top cluster terms (Title_Case, joined)."""
    picked = [t for t in terms if len(t) > 2][:2]
    if not picked:
        return ""
    return "_".join(w.capitalize() for w in picked)


def _llm_name(llm_client, terms: List[str], subjects: List[str]) -> Optional[Dict[str, str]]:
    """Ask the LLM for a short label name + one-line rationale for a cluster."""
    try:
        sample = "\n".join(f"- {s}" for s in subjects[:6] if s)
        kw = ", ".join(terms[:10])
        prompt = (
            "These emails (mostly Hungarian) form one recurring theme the user has "
            "no label for.\n"
            f"Top keywords: {kw}\n"
            f"Example subjects:\n{sample}\n\n"
            "Propose ONE short, meaningful English label (1-2 words, Title_Case, no "
            "spaces — use _) describing the TOPIC (ignore greetings/filler words), "
            "plus a one-line rationale (max 90 chars). Respond EXACTLY as:\n"
            "LABEL: <label>\nWHY: <rationale>"
        )
        content = _chat_complete(
            llm_client,
            "You name email categories concisely by topic.",
            prompt,
            max_tokens=60,
        ).strip()
        label, why = "", ""
        for line in content.splitlines():
            if line.upper().startswith("LABEL:"):
                label = line.split(":", 1)[1].strip().replace(" ", "_")
            elif line.upper().startswith("WHY:"):
                why = line.split(":", 1)[1].strip()
        return {"label": label, "why": why} if label else None
    except Exception as exc:
        LOG.warning("LLM cluster naming failed: %s", exc)
        return None


def suggest_labels(
    db: Database,
    window_days: int = 60,
    account: Optional[str] = None,
    max_suggestions: int = 5,
    min_cluster_size: int = 6,
    llm_client=None,
) -> List[Dict[str, Any]]:
    """Discover and persist new label suggestions. Returns the rows it inserted."""
    rows = _fetch_window(db, window_days, account)
    if len(rows) < min_cluster_size * 2:
        LOG.info("Label discovery: only %d candidate emails in window; skipping.", len(rows))
        return []

    corpus, meta = [], []
    for r in rows:
        text = build_content_text(r.get("subject") or "", r.get("snippet") or "",
                                  r.get("body_text") or "")
        if text and len(text) > 8:
            corpus.append(text)
            meta.append(r)
    if len(corpus) < min_cluster_size * 2:
        LOG.info("Label discovery: too little usable text (%d); skipping.", len(corpus))
        return []

    # Lazy heavy imports so importing this module stays cheap.
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    import numpy as np

    vec = TfidfVectorizer(max_features=2000, stop_words=_stop_words(),
                          token_pattern=r"(?u)[a-z][a-z0-9]{2,}", lowercase=True)
    try:
        X = vec.fit_transform(corpus)
    except ValueError:
        LOG.info("Label discovery: empty vocabulary; skipping.")
        return []

    n = X.shape[0]
    k = max(2, min(8, n // 30))
    km = KMeans(n_clusters=k, n_init=10, random_state=42)
    assign = km.fit_predict(X)
    terms_arr = np.array(vec.get_feature_names_out())
    centroids = km.cluster_centers_

    in_use = get_in_use_labels(db)
    existing_suggestions = {
        s["suggested_label"].strip().lower()
        for s in get_label_suggestions(db, status="pending")
    } | {
        s["suggested_label"].strip().lower()
        for s in get_label_suggestions(db, status="accepted")
    }

    # Rank clusters by size (descending) and propose the largest coherent ones.
    candidates = []
    for c in range(k):
        idx = [i for i, a in enumerate(assign) if a == c]
        if len(idx) < min_cluster_size:
            continue
        top_term_idx = centroids[c].argsort()[::-1][:10]
        terms = [t for t in terms_arr[top_term_idx].tolist() if _TOKEN_RE.fullmatch(t)]
        if not terms:
            continue
        subjects = [meta[i].get("subject") or "" for i in idx]
        examples = [meta[i]["gmail_id"] for i in idx][:8]
        candidates.append({
            "size": len(idx), "terms": terms, "subjects": subjects,
            "examples": examples,
        })
    candidates.sort(key=lambda d: d["size"], reverse=True)

    inserted: List[Dict[str, Any]] = []
    for cand in candidates:
        if len(inserted) >= max_suggestions:
            break
        # Name the cluster (LLM preferred, keyword fallback).
        name, why = "", ""
        if llm_client is not None:
            named = _llm_name(llm_client, cand["terms"], cand["subjects"])
            if named:
                name, why = named["label"], named["why"]
        if not name:
            name = _keyword_name(cand["terms"])
            why = "Recurring theme from top terms: " + ", ".join(cand["terms"][:5])
        if not name:
            continue
        key = name.strip().lower()
        # Skip themes the user already has a label for, or we already proposed.
        if key in in_use or key in existing_suggestions:
            LOG.debug("Label discovery: skipping '%s' (already in use/suggested).", name)
            continue
        score = round(cand["size"] / max(n, 1), 4)
        if save_label_suggestion(
            db, suggested_label=name, rationale=why,
            cluster_terms=", ".join(cand["terms"][:10]),
            example_gmail_ids=cand["examples"], email_count=cand["size"],
            score=score, account=account,
        ):
            existing_suggestions.add(key)
            inserted.append({"label": name, "rationale": why, "size": cand["size"]})
            LOG.info("Label discovery: suggested '%s' (%d emails).", name, cand["size"])

    return inserted
