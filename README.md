# MailMind

A privacy-first Gmail classification and automation assistant. MailMind fetches unread emails, runs them through a three-tier hybrid pipeline (rules → ML → LLM), queues suggested actions for human review, and only writes labels to Gmail after you approve them. All sensitive data stays local.

**Live dashboard:** [mailmind-adam.fly.dev](https://mailmind-adam.fly.dev) (Fly.io, Streamlit)

---

## Features

- **Three-tier hybrid pipeline** — deterministic rules → scikit-learn ML → optional DeepSeek LLM, each stage only activating if confidence is insufficient
- **Human-in-the-loop review queue** — all suggested actions queue for approval before touching Gmail; approve/reject from the dashboard
- **Sender memory** — tracks per-sender trust (trusted / neutral / watchlist) and applies modest score nudges; updates automatically from your approve/reject decisions
- **Thread intelligence** — detects reply-needed emails, waiting-on-other-party, open questions, and extracts thread summaries
- **Explainability** — every queued action stores a full `reason_json` payload (label, confidence, score breakdown, rule matches, trust tier, thread context) shown in the Review tab
- **Idempotent action queue** — SHA-256 fingerprint on every action prevents duplicate queue entries even on repeated pipeline runs
- **Three-tab Streamlit dashboard** — NOW (urgent/reply-needed), REVIEW (full reasoning + approve/reject/edit), AUTOMATE (sender profiles, model health, queue stats)
- **Dry-run default** — `MAILMIND_DRY_RUN=1` everywhere; nothing writes to Gmail without an explicit approve action
- **No Gmail deletes** — delete action requires 1.00 confidence, unreachable by design

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

### Authenticate

Place your OAuth client secret at `~/.mailmind/credentials.json`, then:

```bash
python -m mailmind.main auth
```

A browser window opens for the Google OAuth consent screen. The token is stored in macOS Keychain (or `~/.mailmind/tokens.json.enc` as an encrypted fallback).

### Run

```bash
# One-shot: fetch, classify, queue
python -m mailmind.main run

# Dry-run (no label writes, no queue actions)
python -m mailmind.main run --dry-run

# Continuous watch mode
python -m mailmind.main run --watch --poll-seconds 120
```

### Review dashboard

```bash
streamlit run mailmind/dashboard/app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## Dashboard tabs

| Tab | Purpose |
|---|---|
| **NOW** | High-priority and reply-needed items — single Approve per item |
| **REVIEW** | All pending actions — full reasoning (Why this?) + Approve / Reject / Edit Label |
| **AUTOMATE** | Sender trust profiles, auto-action toggle, model health, queue statistics |

---

## Confidence tier policy

| Tier | Score | Behavior |
|---|---|---|
| Auto-execute | ≥ 0.90 | Action executed immediately |
| Queue for review | 0.65 – 0.90 | Added to human-review queue |
| Skip | < 0.65 | No action taken |
| LLM override | LLM confidence ≥ 0.90 | LLM label overrides rules label |
| Rules skip LLM | Rules score ≥ 70 | LLM not called (cost control) |

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `MAILMIND_DATA_DIR` | `~/.mailmind` | Directory for DB, model, and token files |
| `MAILMIND_DB_PATH` | `~/.mailmind/mailmind.db` | SQLite database path (overrides DATA_DIR) |
| `MAILMIND_POLL_SECONDS` | `120` | Watch-mode poll interval |
| `MAILMIND_FETCH_MAX` | `50` | Max emails fetched per run |
| `MAILMIND_DRY_RUN` | `0` | Set to `1` to suppress all Gmail label writes |
| `MAILMIND_USER_EMAIL` | — | Your primary email for direct-mention scoring bonus |
| `DEEPSEEK_API_KEY` | — | DeepSeek API key; absent → LLM stage disabled |
| `DEEPSEEK_MODEL` | `deepseek-chat` | DeepSeek model name |
| `DEEPSEEK_MAX_CALLS_PER_RUN` | `10` | Max LLM API calls per pipeline run |
| `OPENAI_API_KEY` | — | OpenAI API key for third-tier LLM classifier |
| `LLM_ENABLED` | `false` | Explicitly enable OpenAI-based classifier |

---

## ML training

Once you have ≥ 10 labeled emails processed by the pipeline:

```bash
python -m mailmind.scripts.train_ml_model
```

The model is saved to `$MAILMIND_DATA_DIR/model.pkl`. Accuracy and training stats are displayed in the AUTOMATE tab.

---

## Project structure

```
mailmind/
├── main.py                  # CLI entry point (run / auth commands)
├── config.py                # Environment-based configuration
│
├── ingestion/
│   ├── auth.py              # OAuth2 flow, Keychain/encrypted token storage
│   ├── fetcher.py           # Gmail API batch fetch
│   └── parser.py            # Raw Gmail message → Email model
│
├── processing/
│   ├── pipeline.py          # Orchestrates rules → scorer → thread → ML/LLM → queue
│   ├── queue_manager.py     # Idempotent enqueue with fingerprint dedup
│   ├── rules.py             # Deterministic rule-based classifier
│   └── scorer.py            # Priority scorer (0-100) + sender memory nudge
│
├── intelligence/            # Pass 8 — Copilot Update
│   ├── sender_memory.py     # Sender trust profiles (trusted/neutral/watchlist)
│   ├── thread_analyzer.py   # Heuristic thread / reply-needed detection
│   ├── explainer.py         # ReasonPayload builder → reason_json in queue
│   └── feedback.py          # Approve / reject / correct handlers
│
├── dashboard/               # Three-tab Streamlit review UI
│   ├── app.py               # NOW / REVIEW / AUTOMATE tabs
│   └── helpers.py           # Pure formatting helpers (testable without Streamlit)
│
├── ml/
│   ├── model.py             # scikit-learn model wrapper
│   ├── train.py             # Training from DB labels
│   ├── features.py          # Feature extraction
│   ├── inference.py         # Inference orchestration
│   └── classifier_router.py # Three-tier routing logic
│
├── llm/
│   └── deepseek.py          # DeepSeek LLM client (optional, fail-safe)
│
├── actions/
│   ├── executor.py          # Safe Gmail label executor
│   └── safety.py            # Action policy checks (delete always blocked)
│
├── storage/
│   ├── database.py          # SQLite abstraction (WAL mode)
│   ├── migrations.py        # Linear idempotent migrations (0001–0013)
│   ├── models.py            # Dataclasses: Email, Prediction, QueueItem, …
│   └── queries.py           # All DB query helpers
│
├── utils/
│   └── fingerprint.py       # SHA-256 action fingerprint (dedup)
│
└── tests/                   # 217 pytest tests
```

---

## Running tests

```bash
pytest mailmind/tests/ -v
# 217 passed
```

Tests use in-memory SQLite — no network, no Gmail API, no LLM calls.

---

## Fly.io deployment

The app runs on [Fly.io](https://fly.io) as `mailmind-adam`.

```bash
# Deploy
fly deploy

# View logs
fly logs

# SSH into machine
fly ssh console

# Run pipeline manually on Fly
fly ssh console -C "MAILMIND_DATA_DIR=/data/.mailmind python -m mailmind.main run"

# Train ML model on Fly
fly ssh console -C "MAILMIND_DATA_DIR=/data/.mailmind python -m mailmind.scripts.train_ml_model"

# Check DB
fly ssh console -C "sqlite3 /data/.mailmind/mailmind.db 'SELECT COUNT(*) FROM emails;'"
```

**Critical Fly secret**: `MAILMIND_DATA_DIR` must be set to `/data/.mailmind` (not `~/.mailmind`) so the poller, dashboard, and training script all read the same persistent SQLite file.

The container starts via `fly-start.sh` which:
1. Optionally restores the DB from S3 via Litestream
2. Launches the Streamlit dashboard on `:8501`
3. Starts the MailMind polling daemon

---

## Security notes

- No email body text is logged or shown in the dashboard
- OAuth tokens are stored in macOS Keychain; never in the repo
- `credentials.json` must live at `~/.mailmind/credentials.json`, outside the repo — **never commit it**
- All SQLite writes use parameterised queries
- The delete action is hard-blocked in `SafetyPolicy` regardless of confidence
