"""MailMind — thread and reply-needed intelligence.

Heuristic-first: fast, deterministic, no network calls.
Optional LLM summarization falls back gracefully on any error.

Bilingual: English + Hungarian.

Hungarian grammar notes for the patterns below:
  - Verb conjugations vary heavily; we match on stems where possible.
  - "kérem" = please / I ask  (formal imperative)
  - "tudna" = could you (conditional)
  - "jelezze / jelezzük" = let me/us know
  - "visszaigazol" = confirm  (visszaigazolás = confirmation)
  - "várom" = I'm waiting for
  - "megkapná / megküldené" = could you send
  - "értesít" = notify / let know
  - "hamarosan" = soon
  - "visszajelz" = get back / respond (stem covers visszajelzünk, visszajelzést)
  - "felkeressük / megkeressük" = we will contact you
  - Question markers: "?" works in Hungarian just like English.
    Additionally: "ugye" (right?), "-e" suffix (yes/no question particle)
    but these are hard to regex; "?" coverage is sufficient.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Optional

LOG = logging.getLogger(__name__)


@dataclass
class ThreadContext:
    is_thread:              bool
    thread_length:          int
    reply_needed:           bool
    open_question_detected: bool
    waiting_on_other_party: bool
    thread_summary:         Optional[str] = None
    action_items:           list = None   # list[str]; defaults to [] in analyze()
    deadlines:              list = None   # list[str]; defaults to [] in analyze()


class ThreadAnalyzer:
    """Heuristic-first thread analyzer with optional LLM summarization."""

    # ── Reply-needed signals ─────────────────────────────────────────
    # Compiled once at class definition time for speed.
    # re.I handles Unicode case-folding in Python 3 (É→é, Á→á, etc.)
    _REPLY_RE = re.compile(
        r"(please confirm|can you|let me know|could you|will you|"
        r"would you|please advise|your response|get back to me|"
        r"awaiting your|waiting for your reply|"
        # ── Hungarian ──────────────────────────────────────────────
        r"k[eé]rem (erős[ií]tse|visszaigazol|jelezze|tudassa|k[uü]ld|"
        r"v[eé]lekedj|válaszoljon|válaszolj)"  # kérem erősítse meg / jelezze / küldje
        r"|k[eé]rem sz[ií]veskedjen"           # kérem szíveskedjen + verb
        r"|tudn[aá] (k[uü]ld|seg[ií]t|v[aá]laszol|megerős[ií]t|jelezni)" # tudna küldeni / segíteni
        r"|v[aá]rom .{0,25}(v[aá]lasz[aá]t|visszajelz[eé]s[eé]t|megerős[ií]t[eé]s[eé]t)"  # várom (mielőbbi) válaszát
        r"|k[eé]rn[eé]k (egy v[aá]laszt|visszajelz[eé]st|megerős[ií]t[eé]st)"       # kérnék egy választ
        r"|visszajelz[eé]s[eé]t k[eé]rem"     # visszajelzését kérem
        r"|meg tudn[aá] erős[ií]teni"          # meg tudná erősíteni
        r"|el tudn[aá] k[uü]ldeni"             # el tudná küldeni
        r"|jelezze|jelezzen|jelezd"            # jelezze (let me know — very common)
        r"|tudassa|tudasson|tudasd"            # tudassa (please inform)
        r"|[eé]rtes[ií]tsen|[eé]rtes[ií]tsd"  # értesítsen (please notify)
        r"|v[aá]laszoljon|v[aá]laszolj"        # válaszoljon (please reply)
        r"|sz[ií]veskedjen v[aá]laszolni"      # szíveskedjen válaszolni
        r"|k[eé]rj[uü]k (erős[ií]tse|jelezze|k[uü]ld|v[aá]laszolj)" # kérjük erősítse meg
        r"|k[eé]rdezni szeretn[eé]k"           # kérdezni szeretnék (I'd like to ask)
        r"|k[eé]rd[eé]sem van"                 # kérdésem van (I have a question)
        r"|mi a v[eé]lem[eé]nye|mi a v[eé]lem[eé]nyed)"  # mi a véleménye (what do you think)
        ,
        re.I | re.UNICODE,
    )

    # ── Waiting-on-other-party signals ───────────────────────────────
    _WAITING_RE = re.compile(
        r"(we'll update|i'll get back|i will get back|we will get back|follow up|"
        r"will reach out|we will contact|stay tuned|keep you posted|"
        # ── Hungarian ──────────────────────────────────────────────
        r"hamarosan visszat[eé]r[uü]nk"        # hamarosan visszatérünk (we'll get back soon)
        r"|visszajelz[uü]nk"                   # visszajelzünk (we'll get back to you)
        r"|[eé]rtes[ií]teni fogjuk"            # értesíteni fogjuk (we'll notify you)
        r"|felkeress[uü]k|megkeress[uü]k"      # felkeressük (we'll contact you)
        r"|[eé]rtes[ií]t[eé]st k[uü]ld[uü]nk" # értesítést küldünk (we'll send notification)
        r"|hamarosan [eé]rtes[ií]t[jü]k"       # hamarosan értesítjük (we'll notify you soon)
        r"|dolgozunk rajta"                    # dolgozunk rajta (we're working on it)
        r"|folyamatban van"                    # folyamatban van (in progress)
        r"|[aá]tv[eé]tel[uü]nket megerős[ií]tj[uü]k"  # átvételünket megerősítjük (we confirm receipt)
        r"|hamarosan v[aá]laszolunk"           # hamarosan válaszolunk (we'll reply soon)
        r"|nemsok[aá]ra visszajelz[uü]nk"      # nemsokára visszajelzünk
        r"|munkat[aá]rsunk (felveszi|megkeresi) (önnel|veled)"  # colleague will contact
        r"|k[eé]s[oő]bb visszajelz[uü]nk)"     # később visszajelzünk (we'll get back later)
        ,
        re.I | re.UNICODE,
    )

    # ── Action item signals ─────────────────────────────────────────
    _ACTION_ITEM_RE = re.compile(
        r"(please (review|send|complete|confirm|sign|approve|provide)|"
        r"can you (please )?(send|review|complete|confirm|provide|sign)|"
        r"could you (please )?(send|review|complete|provide)|"
        r"we need you to|action required|"
        # Hungarian imperatives
        r"k[eé]rem (k[uü]ldje|n[eé]zze [aá]t|k[eé]sz[ií]tse|t[oö]ltse ki|"
        r"ír[jJ]a al[aá]|hagyja j[oó]v[aá]|igazolja vissza|biztos[ií]tsa)|"
        r"k[eé]rj[uü]k (k[uü]ldje|t[oö]ltse|er[oő]s[ií]tse)|"
        r"sz[uü]ks[eé]g[uü]nk van|int[eé]zze el|v[eé]gezze el)",
        re.I | re.UNICODE,
    )

    # ── Deadline signals ─────────────────────────────────────────────
    _DEADLINE_RE = re.compile(
        r"(by (monday|tuesday|wednesday|thursday|friday|tomorrow|end of day|eod|"
        r"\d{1,2}(st|nd|rd|th)?)|"
        r"due (date|by|on)|deadline|no later than|"
        # Hungarian: határidő, péntekig, május 3-ig, ISO/dotted dates
        r"hat[aá]rid[oő]|"
        r"\w+ig\b|"                      # -ig suffix (péntekig, holnapig)
        r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}|"  # 2026.06.02 / 2026-06-02
        r"\d{1,2}-[aá]n|\d{1,2}-[eé]n)",     # 3-án, 5-én
        re.I | re.UNICODE,
    )

    # Hungarian "Válasz:" prefix = "Re:" in some email clients
    _HU_REPLY_SUBJECT_RE = re.compile(r"^(re:|fw:|fwd:|v[aá]lasz:|továbbítás:)", re.I | re.UNICODE)

    # Question mark — works the same in Hungarian
    _QUESTION_RE = re.compile(r"\?")

    @staticmethod
    def _extract_lines(body: str, pattern) -> list:
        """Return up to 5 distinct matching sentences/lines, trimmed to 140 chars."""
        out, seen = [], set()
        for raw in re.split(r"[.\n!?]", body):
            line = raw.strip()
            if line and pattern.search(line):
                key = line.lower()[:60]
                if key not in seen:
                    seen.add(key)
                    out.append(line[:140])
            if len(out) >= 5:
                break
        return out

    @classmethod
    def analyze(cls, email, db=None) -> ThreadContext:
        body = (getattr(email, "body_text", None) or "")
        subj = (getattr(email, "subject", None) or "")

        # ── Thread detection ────────────────────────────────────────
        is_thread = (
            bool(getattr(email, "thread_id", None))
            or bool(cls._HU_REPLY_SUBJECT_RE.match(subj))
        )
        thread_length = (body.count("\n>") + body.lower().count("re:") + 1) if is_thread else 1

        # ── Reply needed ────────────────────────────────────────────
        body_lc = body.lower()
        reply_needed = bool(cls._REPLY_RE.search(body_lc))
        if not reply_needed:
            reply_needed = bool(cls._QUESTION_RE.search(body))

        # ── Open question ────────────────────────────────────────────
        open_question_detected = bool(cls._QUESTION_RE.search(body))

        # ── Waiting on other party ───────────────────────────────────
        waiting_on_other_party = bool(cls._WAITING_RE.search(body_lc))

        # ── Action items and deadlines ──────────────────────────────
        action_items = cls._extract_lines(body, cls._ACTION_ITEM_RE)
        deadlines = cls._extract_lines(body, cls._DEADLINE_RE)

        # ── Thread summary (first meaningful line, ≤ 200 chars) ─────
        summary: Optional[str] = None
        try:
            lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
            if lines:
                summary = " ".join(lines)[:200]
        except Exception as exc:
            LOG.debug("Failed to build thread summary: %s", exc)

        return ThreadContext(
            is_thread=is_thread,
            thread_length=max(1, int(thread_length)),
            reply_needed=reply_needed,
            open_question_detected=open_question_detected,
            waiting_on_other_party=waiting_on_other_party,
            thread_summary=summary,
            action_items=action_items,
            deadlines=deadlines,
        )
