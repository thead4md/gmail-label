# MailMind

A privacy-first Gmail classification and automation assistant. MailMind fetches unread emails across one or more mailboxes, runs them through a three-tier hybrid pipeline (rules → ML → LLM), queues suggested actions for human review, and only writes labels to Gmail after you explicitly grant a sender autopilot or click Approve. All sensitive data stays local.

**Bilingual:** all heuristic detection (channels, reply-needed, action items, deadlines) works in **English and Hungarian**.

**Live app:** [mailmind-adam.fly.dev](https://mailmind-adam.fly.dev) (Fly.io — FastAPI + React)

---

## Features

- **Three-tier hybrid pipeline** — deterministic rules → local scikit-learn ML → DeepSeek LLM. Each tier only activates when the previous one isn't confident; when the ML tier handles an email, the paid LLM call is skipped.
- **Closed learning loop** — your corrections in the Review tab override the model's past predictions as training labels; the watch loop auto-retrains weekly (or after N corrections) and hot-reloads the new model with no restart.
- **Earned autopilot** — auto-execute requires BOTH high confidence AND an explicit per-sender opt-in (`auto_action_eligible`). Newly-seen senders always queue for review.
- **Multi-mailbox support** — one app, one watch loop, multiple Gmail accounts. Mailbox switcher; emails/predictions/queue scoped per account; sender trust shared.
- **Human-in-the-loop review queue** — Approve in the app actually mutates Gmail (executes the suggested label/star/archive); SHA-256 fingerprints prevent duplicate queue entries.
- **Watch-loop heartbeat** — the sidebar shows when the watcher last ran; goes red after ~6 min of silence so a hung loop is visible instead of mysterious.
- **Activity digest** — an Automate-page panel + `mailmind digest` CLI: classified / executed / pending / corrections / top labels over any window.
- **Self-maintaining** — daily retention sweep prunes old cached emails + VACUUMs the SQLite file; predictions table is upserted (one row per email, latest wins) so storage stays bounded.
- **Sender memory** — tracks per-sender trust (trusted / neutral / watchlist) and approval/rejection counts; updates automatically from your decisions; shared across mailboxes.
- **Channel detection** — every email is classified into a communication channel (newsletter / transactional / team / personal / marketing / automated) by fast bilingual heuristics, surfaced as colour chips in the UI and charted in INSIGHTS.
- **Thread intelligence** — detects reply-needed emails, waiting-on-other-party, open questions, extracts thread summaries, and pulls out **action items** and **deadlines** (e.g. "by Friday", "péntekig", `2026.06.15`).
- **New-sender screening** — first-time senders surface in a dedicated REVIEW section with one-click **Know / Mute / Block** (Block also rejects all their pending items).
- **Analytics (INSIGHTS tab)** — charts for label distribution, channel volume, channel × weekday heatmap, top senders by approval rate, and time-to-decision histogram.
- **Modern dark UI** — card-based dark theme, sender avatars, confidence bars + rules→ML→LLM sparkline, animated watcher heartbeat, and a mobile-responsive layout.
- **Explainability** — every queued action stores a full `reason_json` payload (label, confidence, score breakdown, rule matches, trust tier, thread context, action items) shown in the Review tab.
- **Safe by construction** — dry-run mode default everywhere; `delete` action requires 1.00 confidence (unreachable); URGENT/FINANCE/PERSONAL emails cannot be auto-archived; ActionExecutor + SafetyPolicy fully tested.

---

## Quick start

### Prerequisites

- Python 3.11+
- Gmail API credentials (`credentials.json`) from a Google Cloud project with the Gmail API enabled

### Install

```bash
git clone <repo>
cd mailmind
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### Authenticate (single mailbox)

Place your OAuth client secret at `~/.mailmind/credentials.json`, then:

```bash
python -m mailmind.main auth
```

A browser window opens for the Google OAuth consent screen. The token is stored in macOS Keychain (or `~/.mailmind/tokens.json.enc` as an encrypted fallback).

### Authenticate (two mailboxes)

Set `MAILMIND_ACCOUNTS` to the comma-separated list (first entry = primary, reuses the legacy token), then auth each:

```bash
export MAILMIND_ACCOUNTS="alice@primary.com,alice@secondary.org"

python -m mailmind.main auth                              # primary
python -m mailmind.main auth --account alice@secondary.org  # secondary
python -m mailmind.main accounts                          # list + connection status
```

The OAuth consent screen for the second account may need that address added as a Test user in Google Cloud Console (APIs & Services → OAuth consent screen → Test users).

### Run

```bash
# One-shot: fetch, classify, queue for all configured accounts
python -m mailmind.main run

# Dry-run (Gmail mutations suppressed; classifications + queue still happen)
python -m mailmind.main run --dry-run

# Continuous watch mode (auto-retrains, hot-reloads model, heartbeat, retention sweep)
python -m mailmind.main run --watch --poll-seconds 120

# Activity digest
python -m mailmind.main digest --days 7

# Prune local cache + VACUUM (also runs daily inside --watch)
python -m mailmind.main prune --retention-days 90
```

### Web app

The web app is a FastAPI backend (`mailmind/api/`) plus a React frontend (`frontend/`). For local development, run both:

```bash
# Terminal 1 — API
uvicorn mailmind.api.main:app --reload --port 8000

# Terminal 2 — frontend dev server (proxies /api to :8000)
cd frontend && npm install && npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser. For production, `npm run build` outputs static assets that the FastAPI app serves directly (see `mailmind/api/main.py`) — this is what the Dockerfile does.

---

## App pages

| Page | Purpose |
|---|---|
| **Now** | High-priority and reply-needed items as cards — sender avatar, label/channel chips, confidence bar, action-item + deadline chips, single Approve per item |
| **Review** | New-sender screening (Know / Mute / Block) + recent predictions + pending actions with full reasoning (Why this?), and Approve / Reject / Edit Label |
| **Inbox / Search / Folders** | Master-detail mail browsing — message list + reading pane, thread view, bulk label/archive, and the three-step reply compose gate (Save Draft → Approve → Send) |
| **Insights** | Analytics charts — label distribution, channel volume, channel × weekday heatmap, top senders, time-to-decision |
| **Automate** | Activity digest, sender trust profiles, **per-sender autopilot toggles**, model health, queue statistics |

The sidebar shows:
- **Mailbox switcher** (when >1 account is configured)
- **Watcher status** (`✅ active <X> ago` / `⚠️ silent for <X>` / `⏳ no heartbeat yet`)

---

## Confidence + autopilot policy

| Tier | Confidence | Sender state | Behavior |
|---|---|---|---|
| Auto-execute | ≥ 0.90 | `auto_action_eligible = 1` | Fires immediately against Gmail |
| Queue for review | ≥ 0.90 | not eligible | Goes to the Review queue |
| Queue for review | 0.65 – 0.90 | any | Goes to the Review queue |
| Skip | < 0.65 | any | No action recorded |
| LLM override | LLM conf ≥ 0.90 | — | LLM label overrides rules label |
| Rules skip LLM | rules score ≥ 70 | — | Paid LLM not called (cost control) |
| ML skip LLM | ML handled email | — | Paid LLM not called (cost control) |

**Earned autopilot:** the 0.90-confidence floor *plus* per-sender opt-in is intentional — automation is something you grant per sender via the AUTOMATE tab, not the default. See `CONTEXT.md` → Decisions Log for the rationale.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `MAILMIND_DATA_DIR` | `~/.mailmind` | Directory for DB, model, and token files |
| `MAILMIND_DB_PATH` | `$MAILMIND_DATA_DIR/mailmind.db` | SQLite database path |
| `MAILMIND_ACCOUNTS` | `[MAILMIND_USER_EMAIL]` | Comma-separated mailbox emails; first is primary |
| `MAILMIND_USER_EMAIL` | — | Your email for direct-mention scoring bonus |
| `MAILMIND_POLL_SECONDS` | `120` | Watch-mode poll interval |
| `MAILMIND_FETCH_MAX` | `50` | Max emails fetched per account per cycle |
| `MAILMIND_DRY_RUN` | `0` | Set to `1` to suppress all Gmail mutations |
| `MAILMIND_RETENTION_DAYS` | `90` | Local-cache retention window for the daily prune |
| `DEEPSEEK_API_KEY` | — | DeepSeek API key; absent → LLM stage disabled |
| `DEEPSEEK_MODEL` | `deepseek-chat` | DeepSeek model name |
| `DEEPSEEK_MAX_CALLS_PER_RUN` | `10` | Max LLM calls per cycle (cost cap) |
| `DASHBOARD_PASSWORD` | — | If set, the web app requires this password |
| `DASHBOARD_SECRET` | — | Random key (e.g. `openssl rand -hex 32`) used to sign the auth cookie; falls back to DASHBOARD_PASSWORD if unset |
| `GMAIL_TOKEN` | — | Headless OAuth token for primary mailbox (Fly secret) |
| `GMAIL_TOKEN_<SLUG>` | — | Headless token for secondary mailbox, e.g. `GMAIL_TOKEN_ALICE_SECONDARY_ORG` |

---

## ML training

Training is automatic. The watch loop calls `_maybe_retrain()` each cycle; it fires when:

- It's been **≥ 7 days** since the last retrain (cadence), OR
- You've logged **≥ 5 new corrections** since the last retrain (signal)

State is tracked in `system_state`. The next watch cycle hot-reloads `pass4_baseline.joblib` automatically (detected via mtime — no process restart needed).

The corrections you log in the Review tab override the model's past predictions as training labels, so retraining moves the model toward your judgment rather than reinforcing its past guesses.

**Manual retrain** (e.g. after a one-off correction sweep):

```bash
python -m mailmind.scripts.train_ml_model
```

Model lands at `$MAILMIND_DATA_DIR/models/pass4_baseline.joblib`. Stats appear in AUTOMATE → Model Health.

---

## Project structure

```
mailmind/
├── main.py                  # CLI entry point: run / auth / accounts / digest / prune
├── config.py                # Environment-based configuration
│
├── ingestion/
│   ├── auth.py              # Per-account OAuth2; Keychain/encrypted/env-var token storage
│   ├── fetcher.py           # Gmail API batch fetch
│   └── parser.py            # Raw Gmail message → Email model
│
├── processing/
│   ├── pipeline.py          # Orchestrates rules → ML router → DeepSeek → queue
│   ├── queue_manager.py     # Earned-autopilot gate + idempotent enqueue
│   ├── rules.py             # Deterministic rule-based classifier
│   └── scorer.py            # Priority scorer (0-100) + sender memory nudge
│
├── intelligence/
│   ├── sender_memory.py     # Sender trust profiles (trusted/neutral/watchlist)
│   ├── channels.py          # Bilingual channel detection (newsletter/team/…)
│   ├── thread_analyzer.py   # Bilingual reply-needed + action item / deadline extraction
│   ├── explainer.py         # ReasonPayload builder → reason_json in queue
│   └── feedback.py          # Approve (executes!) / reject / correct + Know/Mute/Block
│
├── api/                     # FastAPI backend — thin HTTP wrapper over the
│   ├── main.py              #   modules above (no business logic of its own);
│   ├── auth.py              #   also serves the built React SPA in production
│   └── routers/             # one router per page: now, review, inbox, search,
│                             #   folders, history, insights, automate, drafts
│
├── ml/
│   ├── model.py             # scikit-learn model wrapper
│   ├── train.py             # Training from DB labels (corrections override)
│   ├── features.py          # Feature extraction
│   ├── inference.py         # Inference orchestration
│   └── classifier_router.py # Three-tier routing logic (rules → ML → LLM)
│
├── llm/
│   └── deepseek.py          # DeepSeek LLM client (optional, fail-safe)
│
├── actions/
│   ├── executor.py          # Safe Gmail label executor (fully tested)
│   └── safety.py            # Action policy checks (fully tested)
│
├── storage/
│   ├── database.py          # SQLite abstraction (WAL mode, upserts, prune, vacuum)
│   ├── migrations.py        # Linear idempotent migrations (0001–0016)
│   ├── models.py            # Dataclasses: Email, Prediction, QueueItem (account-aware)
│   └── queries.py           # All DB query helpers (account-scoped + analytics)
│
├── utils/
│   └── fingerprint.py       # SHA-256 action fingerprint (dedup)
│
└── tests/                   # 697 pytest tests
```

---

## Running tests

```bash
pytest mailmind/tests/ -q
# 697 passed
```

Tests use in-memory SQLite — no network, no Gmail API, no LLM calls. The ActionExecutor and SafetyPolicy paths (which actually mutate Gmail in production) have dedicated test files covering dry-run, protected categories, delete-blocked, rate limit, and every supported action.

---

## Fly.io deployment

The app runs on [Fly.io](https://fly.io) as `mailmind-adam`. Single machine, persistent volume at `/data`, the FastAPI app (serving both the API and the built React frontend) exposed on port 443.

### Required Fly secrets

```bash
fly secrets set \
  MAILMIND_USER_EMAIL=you@example.com \
  MAILMIND_DATA_DIR=/data/.mailmind \
  DEEPSEEK_API_KEY=sk-... \
  DASHBOARD_PASSWORD=$(openssl rand -hex 16) \
  DASHBOARD_SECRET=$(openssl rand -hex 32) \
  GMAIL_TOKEN="$(cat ~/.mailmind/token.json)" \
  --app mailmind-adam
```

For a second mailbox add `MAILMIND_ACCOUNTS="primary@x.com,secondary@y.org"` plus a `GMAIL_TOKEN_SECONDARY_Y_ORG` secret (slug = uppercase email with non-alphanumerics → `_`).

### Deploy

```bash
fly deploy --app mailmind-adam
```

The container starts via `fly-start.sh` which:
1. Optionally restores the DB from S3 via Litestream (when `LITESTREAM_*` secrets are set)
2. Launches the FastAPI web app (`uvicorn mailmind.api.main:app`) on `:8501`
3. Starts the MailMind polling daemon (`run --watch`) — unless `MAILMIND_RUN_MODE=external`, in which case the watch loop is skipped and `POST /internal/poll` drives cycles instead (see `fly.toml`'s comments and `.github/workflows/poll-trigger.yml`)

See [`docs/backups.md`](docs/backups.md) for the Litestream restore / disaster-recovery procedure.

### Operational commands

```bash
fly logs --app mailmind-adam                                              # live logs
fly ssh console --app mailmind-adam -C "python -m mailmind.main accounts" # connection status
fly ssh console --app mailmind-adam -C "python -m mailmind.main digest"   # activity summary
fly ssh console --app mailmind-adam -C "sqlite3 /data/mailmind.db 'SELECT COUNT(*) FROM emails;'"
```

---

## Security notes

- No email body text is logged to stdout or shown in the UI's tables
- OAuth tokens are stored in macOS Keychain locally; on Fly via per-account `GMAIL_TOKEN[_<SLUG>]` secrets (encrypted at rest)
- `credentials.json` lives at `~/.mailmind/credentials.json`, **never** in the repo
- All SQLite writes use parameterised queries
- The `delete` action is hard-blocked in `SafetyPolicy` regardless of confidence
- Auto-execute requires explicit per-sender opt-in — high confidence alone is not sufficient
- Protected categories (`URGENT`, `FINANCE`, `PERSONAL`) cannot be auto-archived
- The web app is password-protected when `DASHBOARD_PASSWORD` is set
- The auth cookie is signed with `DASHBOARD_SECRET` (independent of the password); login attempts are rate-limited to 5 failures per 5 minutes
