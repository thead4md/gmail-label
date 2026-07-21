# MailMind → **Loops**: Product Strategy & Transformation

*Staff-level product + engineering teardown and redesign. Grounded in the actual
codebase at `claude/mailmind-client-strategy-xynet1` (FastAPI + React 19/Vite, three-tier
rules→ML→DeepSeek pipeline, 779 tests). This is not aspirational hand-waving — every
recommendation names the module it builds on, and Section 3's core is **already shipped in
this branch** as V1.*

**North-star thesis:** MailMind should stop being *"a classifier whose homework you review"*
and become an **AI Chief of Staff** whose one job is to **close your open loops** — everything
you owe, and everything you're owed. The primary object of the product is no longer the
*message* or the *label*; it's the **Loop**. The metric that replaces "inbox zero" is
**open loops = 0**.

---

## 1. Deep product teardown

### 1.1 What MailMind is today (honest baseline)
A privacy-first, **single-user, single-machine** Gmail triage engine for one real operator
(a bilingual EN/HU scouting-org leader). It fetches unread mail, classifies each message
through `rules → scikit-learn (TF-IDF+LogReg) → DeepSeek`, scores priority (0–100), and queues
suggested label/star/archive actions for human review. Autopilot executes only for senders the
user has explicitly opted in (`sender_profiles.auto_action_eligible`) at confidence ≥ 0.90.
It is unusually well-built: dry-run default, delete hard-blocked, a closed ML learning loop
(`user_corrections` → weekly retrain → hot-reload), sender trust memory, bilingual heuristics,
and 779 hermetic tests.

### 1.2 UX & interaction
- **Navigation model:** message/label-centric. Nine routes (`/now`, `/review`, `/inbox`,
  `/search`, `/folders`, `/history`, `/insights`, `/automate`); `/now` is the front door but is a
  **flat single column of suggestion cards** (`frontend/src/pages/NowPage.tsx`) — the mental model
  is "approve the classifier's guesses," not "run my inbox."
- **Cognitive load:** the user still has to decide *what matters* by scanning cards. The product
  understands reply-needed / waiting-on / deadlines (`intelligence/thread_analyzer.py`) but **buries
  those signals as per-card chips** instead of organizing the whole screen around them.
- **Speed perception:** 30 s polling (`useNow` `refetchInterval: 30_000`), optimistic mutations via
  React Query (good), but no keyboard flow and no live push. The daily brief is correctly split to a
  slower endpoint (`/api/now/brief`) so the feed never blocks on the LLM.
- **Information density vs clarity:** two competing list paradigms — rich `MessageCard` (Now/Review)
  vs compact `MessageListRow` + reading pane (Inbox/Search/Folders). No single "home."
- **Keyboard-first workflows:** **essentially absent.** The *entire* keyboard surface was ⌘K / Esc
  (`CommandPalette.tsx`). No `j/k`, no `e`, no `r`. For a product that wants to beat Superhuman, this
  was the single biggest, cheapest gap.

### 1.3 AI integration — central or bolted-on?
**Deliberately bolted-on, and deliberately minimal.** The architecture is engineered to *avoid* the
LLM: it's the last tier, capped at 10 calls/run, fed ≤ 500 chars of body, and skipped whenever rules
or ML are confident (`processing/pipeline.py`, `ml/classifier_router.py`). Generative AI appears in
exactly five narrow places: classification fallback, a 1–2 sentence thread summary, a 3-bullet daily
brief (`intelligence/brief.py`), a human-triggered draft reply capped at $0.50/day
(`intelligence/draft_reply.py`), and NL→rule parsing (`intelligence/nl_rules.py`). All prompts are
static single-shot strings — **no few-shot, no tool use, no streaming, no memory, no embeddings
anywhere.** This is excellent *cost engineering* and a weak *product surface*: the AI explains and
sorts, it doesn't act or remember.

### 1.4 Mental model — inbox, task manager, or OS?
Today it's a **triage-and-label console**. It borrowed the "NOW / what needs me" idea but stopped at
presenting a ranked queue. It is neither a real task manager (no durable task/commitment object) nor
an OS (no cross-thread state). The model doesn't match how an overloaded operator actually thinks:
*"who am I blocking, and who's blocking me?"*

### 1.5 Differentiation — why does this exist vs Gmail + Notion?
Today's honest answer is thin: *"Gmail that auto-labels, privately, in Hungarian too."* Real, but
niche. The **missing 10x** is a different *primary object*. Gmail organizes messages; Notion organizes
docs; neither tracks **commitments across threads**. MailMind already computes the raw material for
that (reply-needed, waiting-on, deadlines, sender trust) and throws it away as decoration. That's the
opening.

---

## 2. Reframe the product

### The pick: **B — "AI Chief of Staff for your inbox"**, realized through a new primary object: the **Open Loop**.

**Why B, and why a new object beats A/C/D/E:**
- **A ("Email as a Task OS") / D ("Linear for email")** — turns email into another task list. It's
  incrementalist, crowded (Superhuman splits, Spark smart inbox, Notion Mail), and it silently drops
  the half of the problem where dropped balls actually live: *the replies you're waiting on.*
- **C ("Autonomous communication agent")** — trust-hostile. Email is high-stakes; one wrong auto-send
  is fatal to trust, and it fights MailMind's entire earned-autopilot DNA.
- **E (new category)** — the reframe *is* the new category, but it needs a trust-forward name to sell.
  "Chief of Staff" is that name: it **proposes, you approve** — exactly what the existing
  queue + `SafetyPolicy` + earned-autopilot substrate already implements. The Open Loop is the
  *mechanism* that makes "chief of staff" concrete instead of a tagline.

**The Open Loop model.** Every consequential email is one of three things:

| Loop side | Meaning | Source signal (already in code) |
|---|---|---|
| **You owe** | a reply / decision / task on your side | `reason_json.reply_needed`, pending `action_queue` |
| **Waiting on** | someone owes *you* a reply | new: `loops` table, sent-without-reply detector |
| **Handled / noise** | already filed or auto-actioned | executed `action_queue` rows |

- **Core user promise (one sentence):** *"MailMind closes your loops — it shows exactly what you owe,
  chases what you're owed, and never lets a ball drop."*
- **Primary persona (specific):** the **overloaded operator-leader** — runs an organization, lives in
  email, bilingual, no human assistant, high inbound, personally on the hook for follow-through. (This
  is literally the current user.) Not "inbox-zero hobbyists"; people whose *reputation* depends on not
  dropping threads.
- **The "aha" in < 5 seconds:** on load, one line — **"You owe 4 · 3 people owe you · 2 about to slip"** —
  with the specific items one glance below. Not 2,000 unread. Not "inbox zero in 3 weeks." The instant
  proof that it read everything and told you the only thing that matters.

---

## 3. First-in-class experience  *(V1 of this is shipped in this branch)*

### 3.1 New information architecture
`/now` is reborn as **Loops** — a three-lane board that replaces the flat feed as the front door:

```
┌ LOOPS ───────────────────────────────────────────────────────────────┐
│  You owe 4 · 3 waiting on you · 2 about to slip        [?] shortcuts   │
│  [Triaged 12] [Auto-labeled 7] [Awaiting 4] [Reply needed 3]          │
│                                                                        │
│  ✍️  YOU OWE (4) — a reply or a decision            ← keyboard target  │
│    ▸ ⌷ Bob Nagy   Re: budget sign-off   ⏰ by Friday   [Reply][✓][✗]   │
│    ▸   Stripe     Invoice #4021 overdue               [ label ][✓][✗]  │
│                                                                        │
│  ⏳  WAITING ON (3) — someone owes you a reply                          │
│    ▸ Alice K.     proposal follow-up      waiting 6d  ‹slip› [Nudge]   │
│    ▸ vendor@x     quote request           waiting 2d        [Nudge]    │
│                                                                        │
│  ✅  Handled (7)                                             ⌄ expand   │
└────────────────────────────────────────────────────────────────────────┘
```

- **You owe** = the existing `filter_now_items(get_pending_queue_enriched(...))` — reply-needed +
  high-priority pending items, reframed as loops you must close. **Zero backend logic change.**
- **Waiting on** = the new durable object: `loops` table (side `waiting_on`), populated by a
  deterministic detector (`intelligence/loops.py`) over the already-mirrored SENT mail — a thread whose
  newest message is outbound with no reply back. Annotated with `waiting_days` and a `slipping` flag.
- **Handled** = recent executed/auto-labeled items (`get_executed_queue_enriched`), collapsed.

### 3.2 The 5 core flows (keyboard-first)
1. **Triage** — `j/k` move through *You owe*; `e` approve, `x` reject, `r` reply. The whole lane can be
   cleared without touching the mouse.
2. **Replying** — `r` opens the 3-step compose gate (`ComposeSheet`: Save → Approve → Send). "Draft with
   AI" pre-fills in the user's language (`draft_reply.py`). *(V2: ⌘Enter to advance a step.)*
3. **Follow-ups** — a *Waiting on* card's **Nudge** opens a pre-filled follow-up to the contact.
   *(V2: Loop Radar drafts + sends + tracks it autonomously — Section 5.)*
4. **Task execution** — approving a *You owe* loop executes the real Gmail action through the existing
   `SafetyPolicy`; corrections feed the learning loop unchanged.
5. **Search / recall** — today keyword search. *(V2: semantic recall — "how did I answer this vendor last
   time?" — via `sqlite-vec`, Section 6.)*

### 3.3 Interaction principles
- **Keyboard-first:** yes — the biggest craft gap, now seeded (`useNow` board + global `?` help overlay).
- **AI-first but trust-gated:** AI proposes (draft, nudge, label); the human (or an *earned* autopilot)
  approves. This is the Cursor accept/reject surface applied to email.
- **Not "zero inbox" — "zero open loops."** The success state isn't an empty list; it's *"nothing you owe,
  nobody kept waiting."* The empty state literally says so.

---

## 4. AI-native features (go hard)

Each maps onto an existing module so it's buildable, not fantasy.

### 4.1 Autonomous follow-up agent — *the wow feature (Section 5)*
See below. Builds on `loops` + `draft_reply` + earned autopilot + `send_message`.

### 4.2 Context-aware reply drafting with per-sender voice memory
- **What:** drafts in *your* voice, conditioned on your past sent replies to *this* sender and thread.
- **10x:** every competitor drafts generically; MailMind drafts like *you* to *this person* because it
  already stores per-sender history and your corrections.
- **UX:** the existing "Draft with AI" button, upgraded — the draft arrives already in your register.
- **Tech:** extend `draft_reply.py` to retrieve the user's last N sent messages to the sender (from the
  SENT mirror) as few-shot style exemplars; add prompt caching for the system+style block.

### 4.3 Relationship graph (who matters most)
- **What:** rank contacts by reciprocity, response SLA, and trust; surface VIP loops first.
- **10x:** Superhuman's VIPs are manual; MailMind's are *learned* from `sender_profiles` +
  approval/rejection history + response latency.
- **Tech:** aggregate over `sender_profiles` + thread timing; a nightly `intelligence` job writes a
  `contact_rank`. Feeds loop ordering.

### 4.4 Deadline inference → auto-scheduling
- **What:** `_DEADLINE_RE` already extracts "by Friday / péntekig / 2026.06.15"; turn a detected deadline
  into a one-click calendar hold (proposed, you approve).
- **Tech:** deadline → `create_event` tool call gated by `SafetyPolicy`; Calendar MCP is available in this
  environment.

### 4.5 Thread → project conversion
- **What:** promote a long multi-party thread into a mini-project (participants, open questions, action
  items, deadline) — a durable `loops` cluster.
- **Tech:** `thread_analyzer` already extracts action_items/open_questions; group by `thread_id` into a
  parent loop with children.

### 4.6 Inbox simulation — "what breaks if I ignore this week?"
- **What:** a forward projection: which loops will slip, which relationships decay, which deadlines pass.
- **10x:** nobody does consequence-modeling of an inbox. It turns anxiety ("I'm behind") into a ranked,
  finite list ("these 3 things break Thursday").
- **Tech:** score open loops by `due_ts` proximity × contact_rank × historical slip cost; render a timeline.

---

## 5. The WOW feature — **Loop Radar**: the AI that closes loops without you

> *"Superhuman reminds you to follow up. MailMind follows up, tracks the reply, and closes the loop —
> and only bothers you if it stalls."*

**What it does.** For every *waiting-on* loop, Loop Radar:
1. Detects the stall (already: `loops.due_ts` past → `slipping`).
2. Drafts a context-aware nudge in your voice (§4.2).
3. **For earned-autopilot contacts only**, sends it at a sensible time; for everyone else, queues the
   draft for one-tap approval.
4. Tracks the thread — when a reply lands, the detector auto-closes the loop (already implemented and
   tested: an inbound reply flips the thread and `close_loop` fires).
5. Escalates to you *only* if the nudge itself goes unanswered past a second threshold.

**UX flow.**
```
Loop stale (6d) ──▶ Radar drafts nudge ──▶ earned-autopilot? ──yes──▶ send @ 9:00 local ──▶ track
                                             │                                               │
                                             └──no──▶ queue draft in "You owe" (one-tap send) │
                          reply lands ◀───────────────── auto-close loop ◀────────────────────┘
                          no reply after 2nd window ──▶ escalate: "Alice still hasn't replied — nudge again?"
```

**Backend logic (all substrate exists):** `loops` (state machine: `open → nudged → closed`, with the
reserved `nudge_count`, `last_nudge_ts`, `draft_id` columns already in migration 0032) + `draft_reply`
+ `handle_approve_and_send` (the sole sanctioned `send_message` caller) + `SafetyPolicy` (never
auto-send to a non-eligible sender) + the watch loop's scheduled-draft sweep (`_maybe_send_scheduled_drafts`).

**Why competitors can't easily replicate it.** It requires four things at once that no incumbent has
together: (1) durable **cross-thread loop state**, (2) **per-sender trust + tone memory**, (3) an
**earned-autopilot trust model** that makes autonomous send safe, and (4) a **learning loop** that keeps
tone/nudging calibrated. Superhuman's "remind me if no reply" only pings *you* — it never drafts, sends,
tracks, or closes. Gmail/Gemini has no cross-thread commitment state at all.

---

## 6. Tech architecture

### 6.1 Frontend
- **Framework:** keep **React 19 + Vite + Tailwind v4 + TanStack Query**. It's already clean and small
  (~3k LOC); a Next.js/RSC rewrite buys nothing for a single-machine deploy and would throw away working
  code. State stays React Query (server) + Context (compose) + `useState` (UI).
- **Real-time:** replace 30 s polling with **Server-Sent Events** from FastAPI (one `GET /api/stream`
  yielding queue/loop deltas). SSE (not WebSockets) fits a single uvicorn process and needs no new infra.
- **Perceived speed:** optimistic UI everywhere (have), plus **instant local heuristics** for feedback
  while the LLM works in the background, and prefetch of the next likely loop.

### 6.2 Backend
- Keep **FastAPI + SQLite (WAL)**. Add the `loops` table (done) and the deterministic detector (done);
  Loop Radar adds a state-machine sweep on the existing watch loop.
- **Two-speed AI:** (a) *instant* — the bilingual regex + TF-IDF/LogReg tiers already give sub-second
  labels and loop signals; (b) *background* — DeepSeek (or a stronger reasoning model) for drafting and
  loop reasoning, never on the user's critical path.

### 6.3 AI system
- **Models:** keep DeepSeek as the cheap default; make the model a config seam (already abstracted behind
  `llm/chat.py` + the `LLMClassifier` protocol) so a stronger reasoning model can be swapped for drafting
  and Loop Radar without touching call sites.
- **Memory:** today there is **none** beyond `sender_profiles` counters. Add a lightweight **retrieval
  memory** with `sqlite-vec` (embeddings in the same SQLite file — no external vector DB, fits the box):
  index sent/received bodies for semantic recall and few-shot draft exemplars. Short-term = thread context
  (`thread_context_json`); long-term = per-contact embeddings + correction-derived priors.
- **Prompting vs fine-tuning:** stay prompt-driven. The ML tier already personalizes via the correction
  loop; layer **few-shot from the user's own corrections/sent mail** + **prompt caching** for the static
  system block (50–80% input-token cut). Fine-tuning is a V3+ option, not needed to win.

### 6.4 Performance / sub-100ms perceived latency
Optimistic mutations (have) → instant heuristic labels → SSE push → background LLM with streamed drafts →
aggressive query caching + prefetch. The user *never* waits on a model to see the board move.

---

## 7. Competitive edge (brutally honest)

| vs | Where they win today | Where **Loops** wins |
|---|---|---|
| **Gmail (+Gemini)** | Native, infinite scale, Drive/Calendar context | Gmail shows *messages*; it has **no memory of what you promised or are owed**. Loops is the commitment layer Gmail structurally lacks. |
| **Superhuman** | Speed, keyboard craft, polish (we're behind on craft) | "Remind me" only pings *you*; **Loop Radar closes the loop**. Superhuman still assumes you read everything. Also: private/self-hostable, ~free to run. |
| **Spark / Notion Mail** | Smart inbox, collaboration, doc-context drafting | Still message-centric; no autonomous loop-closing, no earned-autopilot trust model, weaker safety story. |
| **Shortwave / Fyxer** | Best-in-class drafting + full autonomy, zero setup | They out-feature us on drafting — **don't fight there.** We win on **trust + locality + bilingual + the loop model**: automation you *grant per sender*, auditable, on hardware you control. |
| **Inbox Zero / Zero (OSS)** | More features, local LLM option, community | More rigorous **earned-autopilot + safety-by-construction**, the loop reframe, and EN/HU bilingual heuristics no US tool has. |

**Honest weaknesses to hold in view:** (1) it's a single-user box today — "beat Superhuman" is a vision,
not next week; (2) trust is the whole game, so autonomy must stay *earned* — never a default; (3) DeepSeek
latency/quality is a real ceiling for drafting — mitigate with background generation + a stronger model
seam; (4) we are genuinely behind Superhuman on *interaction craft* — V1's keyboard layer starts closing
that, but it's a sustained investment.

---

## 8. Ship plan

### V1 — 2 weeks *(built in this branch)*
- **Ship:** the Loops reframe on `/now` (three lanes: You owe / Waiting on / Handled), the `loops` table +
  deterministic `waiting_on` detector on the watch loop, keyboard-first triage (`j/k/e/x/r`, `?` help), the
  aha summary line, prefilled Nudge.
- **Tradeoffs:** `waiting_on` is high-precision "I sent, no reply" only (no NLP fold-in yet); polling
  retained; no autonomy.
- **Ignore:** autonomous send, embeddings, calendar writes, multi-tenant, mobile, re-theme.

### V2 — 6 weeks
- **Ship:** **Loop Radar** (draft → earned-autopilot send → track → auto-close → escalate); per-sender
  voice drafting (§4.2); semantic recall via `sqlite-vec`; SSE live updates; deadline → calendar hold;
  ⌘Enter compose flow; prompt caching + concurrent LLM tier.
- **Tradeoffs:** autonomy stays strictly gated to eligible senders; recall indexes locally only.
- **Ignore:** teams, mobile app, fine-tuning.

### V3 — 3 months
- **Ship:** relationship graph + VIP loop ranking (§4.3); "what breaks if I ignore this week" inbox
  simulation (§4.6); thread → project (§4.5); deeper agent autonomy with a full audit log; PWA/mobile;
  **optional** multi-tenant re-platform (Section 9) *iff* productizing.
- **Ignore:** anything that trades away the trust/privacy wedge for feature parity with Shortwave.

---

## 9. Multi-tenant fork points (kept open, not built)

The user asked to design single-user-first but not foreclose a product. Every V1 choice is additive-safe:

| Single-user today | Fork when multi-tenant | Why it's not foreclosed |
|---|---|---|
| One SQLite (WAL) file | **Postgres** (+ `pgvector` for recall) | All access is via `storage/queries.py` helpers; the `loops` table is `account`-scoped like every other table. |
| One watch loop, one machine | **Per-mailbox jobs on a queue**, stateless workers | The loop detector is a pure function + per-account driver (`detect_waiting_on_loops(db, account)`); trivially parallelizable. |
| 120 s Gmail poll | **Gmail Pub/Sub push** | Ingestion is already isolated in `ingestion/`; push just changes the trigger. |
| In-proc rate limiter (`SafetyPolicy`) | Shared store (Redis/DB) | Single seam to swap. |
| Password + signed cookie | Real multi-user auth/SSO | `api/auth.py` is a single module. |

**The one decision that gates the fork:** stay a best-in-class *personal loop-closer* (lower effort,
clear wedge) **or** productize into a hosted BYO-key tier (re-platform above). V1 commits to neither — it
makes the personal experience excellent while leaving every door open.

---

*Appendix — key modules this strategy builds on:* `processing/pipeline.py`, `processing/queue_manager.py`
(`filter_now_items`), `intelligence/thread_analyzer.py`, `intelligence/loops.py` (new),
`intelligence/draft_reply.py`, `intelligence/sender_memory.py`, `actions/safety.py`,
`storage/migrations.py` (0032), `storage/queries.py` (`upsert_loop`/`get_open_loops`/`close_loop`),
`api/routers/now.py`, and the React `frontend/src/pages/NowPage.tsx` + `components/layout/ShortcutsHelp.tsx`.
