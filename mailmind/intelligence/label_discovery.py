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
import unicodedata
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
# NOTE: text is accent-normalised before TF-IDF (see _normalise()), so only
# ASCII de-accented forms are needed here.
_HUNGARIAN_STOPWORDS = {
    # Greetings / salutations
    "hogy", "nem", "egy", "isten", "szia", "sziasztok", "kedves", "tisztelt",
    "udv", "udvozlettel", "koszonom", "koszi", "helló", "hello", "sziasz",
    # Pronouns / demonstratives
    "ezt", "azt", "ami", "aki", "akik", "amely", "amelyek", "ezek", "azok",
    "ezzel", "ehhez", "ahhoz", "erre", "arra", "ebben", "abban",
    # Common verbs / auxiliaries
    "mint", "csak", "meg", "mar", "vagy", "lesz", "volt", "lenne", "kell",
    "lehet", "lehetne", "van", "nincs", "nincsen", "legyen", "lett",
    "szeretne", "szeretn", "szeretnem",
    # Conjunctions / particles
    "illetve", "valamint", "tehat", "ill", "stb", "pedig", "azonban",
    "viszont", "mivel", "mert", "hiszen", "ugyan", "ugye", "igen", "nem",
    # Postpositions / case suffixes that survive tokenisation as tokens
    "utan", "elott", "soran", "fele", "reszere", "szamara", "eseten",
    "miatt", "kepen", "kent", "hoz", "hez", "tol", "nek", "ben", "ban",
    "bol", "bol", "val", "vel", "ert", "nak", "hoz", "tek", "ket", "sem",
    "bar", "hat", "les", "tun", "jon", "jon", "juk", "juk",
    # Adverbs / fillers
    "minden", "nagyon", "nagys", "sok", "kevés", "keves", "itt", "ott",
    "most", "majd", "mar", "meg", "is", "sem", "ugy", "igy", "ott", "ide",
    "oda", "erre", "arra", "ezert", "azert", "miert", "hogyan", "amikor",
    "akkor", "mindig", "soha", "talán", "talan", "szinte", "inkabb",
    "szerintem", "szerinte", "szoval", "tehat", "ugyan",
    # Web / email noise
    "com", "www", "http", "https", "gmail", "email", "mail", "from",
    "subject", "poszt", "post", "bbl", "okgy", "jus", "besz",
}


# Mailbox-ubiquitous terms. This inbox is mono-domain (a scouting org), so its
# own vocabulary appears in nearly every email and otherwise dominates every
# cluster — producing useless domain-restating suggestions like "Cserkész_Events"
# or "Hungarian_Scouting". Drop them so clusters form around DISTINGUISHING
# sub-themes. (max_df in the vectorizer also strips ubiquitous terms dynamically.)
_DOMAIN_STOPWORDS = {
    "cserkesz", "cserkész", "cserkeszek", "cserkészek", "cserkeszet", "cserkészet",
    "scout", "scouts", "scouting", "mcssz", "csapat", "csapatok",
    "esemeny", "esemény", "esemenyek", "események", "level", "levél", "levelek",
    "magyar", "szovetseg", "szövetség", "orszagos", "országos",
}


def _normalise(text: str) -> str:
    """Strip accents so TF-IDF sees whole words, not accent-split stumps.

    Without this, 'cserkész' tokenises to 'cserk' + 'sz' (the accent splits
    the run), and neither fragment matches our stopword list. After stripping,
    'cserkész' → 'cserkesz' which IS in _DOMAIN_STOPWORDS and gets dropped.
    """
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("ascii")


def _stop_words() -> list:
    """English + Hungarian + domain stopwords as a list for TfidfVectorizer."""
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
    return sorted(set(ENGLISH_STOP_WORDS) | _HUNGARIAN_STOPWORDS | _DOMAIN_STOPWORDS)


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


def _is_valid_label(name: str) -> bool:
    """Reject generated label names that are stopwords, domain words, or too short."""
    stop = _HUNGARIAN_STOPWORDS | _DOMAIN_STOPWORDS
    words = [w.lower() for w in name.replace("_", " ").split() if w]
    if not words:
        return False
    if any(len(w) <= 2 for w in words):
        return False
    if any(w in stop for w in words):
        return False
    return True


def _purge_stale_suggestions(db: Database) -> int:
    """Delete pending suggestions whose label is composed entirely of stopwords/domain words."""
    stop = _HUNGARIAN_STOPWORDS | _DOMAIN_STOPWORDS
    pending = get_label_suggestions(db, status="pending")
    bad_ids = []
    for s in pending:
        words = [w.lower() for w in s["suggested_label"].replace("_", " ").split()]
        if all(w in stop or len(w) <= 2 for w in words):
            bad_ids.append(s["id"])
    if bad_ids:
        db.execute_sql(
            f"DELETE FROM label_suggestions WHERE id IN ({','.join('?' * len(bad_ids))})",
            bad_ids,
        )
        LOG.info("Label discovery: purged %d stale bad suggestion(s).", len(bad_ids))
    return len(bad_ids)


def _keyword_name(terms: List[str]) -> str:
    """Fallback label name from the top cluster terms (Title_Case, joined)."""
    stop = _HUNGARIAN_STOPWORDS | _DOMAIN_STOPWORDS
    valid = [t for t in terms if len(t) >= 4 and t.lower() not in stop][:2]
    if not valid:
        return ""
    return "_".join(w.capitalize() for w in valid)


def _llm_name(
    llm_client,
    terms: List[str],
    subjects: List[str],
    in_use_labels: Optional[List[str]] = None,
) -> Optional[Dict[str, str]]:
    """Ask the LLM for a short label name + one-line rationale for a cluster."""
    try:
        sample = "\n".join(f"- {s}" for s in subjects[:12] if s)
        kw = ", ".join(terms[:10])
        taxonomy_hint = ""
        if in_use_labels:
            taxonomy_hint = (
                f"Labels the user already has: {', '.join(in_use_labels[:20])}.\n"
                "Do NOT suggest anything that overlaps with or is broader than these.\n"
            )
        prompt = (
            "This is a busy person's inbox at a Hungarian scouting organization — "
            "ALL of it is about scouting, so labels like 'Scouting', 'Events' or "
            "'Hungarian_Scouting' are useless (they describe everything).\n"
            f"{taxonomy_hint}"
            "These emails (mostly Hungarian) form one cluster:\n"
            f"Top keywords: {kw}\n"
            f"Example subjects:\n{sample}\n\n"
            "Propose ONE short, SPECIFIC English label (1-2 words, Title_Case, no "
            "spaces — use _) for a DISTINGUISHING sub-theme that would actually help "
            "triage (e.g. Camp_Logistics, Equipment_Orders, Polls, Quizzes).\n"
            "Do NOT use person names, city names, or org abbreviations as labels.\n"
            "If the cluster has no clear specific theme, or is just generic scouting/"
            "announcement/event mail, respond EXACTLY 'LABEL: SKIP'.\n"
            "Otherwise respond EXACTLY as:\nLABEL: <label>\nWHY: <rationale max 90 chars>"
        )
        content = _chat_complete(
            llm_client,
            "You name email categories by specific, distinguishing topic. You "
            "refuse to name over-generic clusters, answering SKIP instead.",
            prompt,
            max_tokens=60,
        ).strip()
        label, why = "", ""
        for line in content.splitlines():
            if line.upper().startswith("LABEL:"):
                label = line.split(":", 1)[1].strip().replace(" ", "_")
            elif line.upper().startswith("WHY:"):
                why = line.split(":", 1)[1].strip()
        if label.upper() == "SKIP" or not label:
            return None  # LLM judged the cluster too generic to be a useful label
        return {"label": label, "why": why}
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
    _purge_stale_suggestions(db)
    rows = _fetch_window(db, window_days, account)
    if len(rows) < min_cluster_size * 2:
        LOG.info("Label discovery: only %d candidate emails in window; skipping.", len(rows))
        return []

    corpus, meta = [], []
    for r in rows:
        text = build_content_text(r.get("subject") or "", r.get("snippet") or "",
                                  r.get("body_text") or "")
        if text and len(text) > 8:
            corpus.append(_normalise(text))
            meta.append(r)
    if len(corpus) < min_cluster_size * 2:
        LOG.info("Label discovery: too little usable text (%d); skipping.", len(corpus))
        return []

    # Lazy heavy imports so importing this module stays cheap.
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    import numpy as np

    # max_df=0.5 drops terms appearing in >50% of the window — i.e. the mailbox's
    # ubiquitous domain vocabulary — so clusters form around distinguishing themes.
    vec = TfidfVectorizer(max_features=2000, stop_words=_stop_words(),
                          token_pattern=r"(?u)[a-z][a-z0-9]{2,}", lowercase=True,
                          max_df=0.5, min_df=2)
    try:
        X = vec.fit_transform(corpus)
    except ValueError:
        LOG.info("Label discovery: empty vocabulary; skipping.")
        return []

    n = X.shape[0]
    # Finer clustering (was min(8, n//30)) so distinct sub-themes separate instead
    # of collapsing into a few giant domain-restating lumps.
    k = max(2, min(16, n // 15))
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

    # A useful new label names a SUB-theme, not "most of the mailbox". Reject
    # clusters that cover too large a share of the window — those are catch-alls
    # (e.g. the 899-email "Cserkész_Events" lump), not actionable categories. The
    # gate only binds at scale (n>=50); on tiny windows a big cluster is a real
    # theme, not a catch-all.
    max_cluster_size = max(min_cluster_size + 1, int(n * 0.30)) if n >= 50 else n

    # Rank clusters by size (descending) and propose the largest coherent ones.
    candidates = []
    for c in range(k):
        idx = [i for i, a in enumerate(assign) if a == c]
        if len(idx) < min_cluster_size or len(idx) > max_cluster_size:
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
        # Name the cluster. When an LLM is available we trust its judgement: a
        # None result means it judged the cluster too generic (SKIP) — drop the
        # candidate rather than papering over it with a keyword name. Only fall
        # back to keyword naming when no LLM is configured at all.
        name, why = "", ""
        if llm_client is not None:
            named = _llm_name(llm_client, cand["terms"], cand["subjects"],
                              in_use_labels=sorted(in_use))
            if not named:
                LOG.debug("Label discovery: LLM skipped a generic cluster (size=%d).",
                          cand["size"])
                continue
            name, why = named["label"], named["why"]
        else:
            name = _keyword_name(cand["terms"])
            why = "Recurring theme from top terms: " + ", ".join(cand["terms"][:5])
        if not name:
            continue
        if not _is_valid_label(name):
            LOG.debug("Label discovery: rejecting invalid label name '%s'.", name)
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
