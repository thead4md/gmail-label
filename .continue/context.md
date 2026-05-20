# MailMind — AI Session Context

## What this repo is
A Python CLI + Streamlit app that authenticates to Gmail via OAuth2,
fetches unread INBOX messages, classifies them with rule-based + ML scoring,
and writes Gmail labels / actions back. Deployed on Fly.io and Render via Docker.
SQLite DB (backed up via Litestream to S3).

## Repo layout
gmail-label/
├── mailmind/               # Main Python package
│   ├── main.py             # CLI entry point (click): `run` + `auth` commands
│   ├── review_dashboard.py # Streamlit dashboard for reviewing predictions
│   ├── ingestion/          # Gmail OAuth2, fetcher, message parser
│   │   ├── auth.py         # authenticate() + build_gmail_service()
│   │   ├── fetcher.py      # GmailFetcher — lists + fetches raw messages
│   │   └── parser.py       # parse_message(raw) → Email model
│   ├── processing/         # Classification pipeline
│   │   ├── pipeline.py     # Pipeline.process(email, auto_action) → Prediction
│   │   ├── rules.py        # RulesEngine — keyword/regex rules
│   │   ├── scorer.py       # PriorityScorer → ScoreResult (JSON breakdown)
│   │   └── queue_manager.py# QueueManager — decides auto-execute vs. queue
│   ├── actions/            # Gmail label writes
│   │   ├── executor.py     # ActionExecutor.execute(prediction, email)
│   │   └── safety.py       # SafetyPolicy(dry_run) — guards real writes
│   ├── storage/            # Persistence
│   │   ├── database.py     # Database — SQLite wrapper
│   │   └── models.py       # Email, Prediction dataclasses
│   ├── ml/                 # ML models (classification)
│   ├── scripts/            # Utility / migration scripts
│   └── tests/              # Test suite
├── Dockerfile              # Production Docker image
├── fly.toml                # Fly.io deployment config
├── render.yaml             # Render deployment config
├── litestream.yml          # Litestream SQLite → S3 replication (prod)
├── litestream-mac.yml      # Litestream config for local macOS dev
├── config.yaml.example     # User config template (labels, rules, etc.)
└── .continue/              # Continue IDE config

## Key data flow
Gmail OAuth → GmailFetcher.list_message_ids()
  → fetcher.get_message(id) → parse_message() → Email
  → Database.insert_email()
  → Pipeline.process(email, auto_action=False) → Prediction
  → QueueManager.enqueue_from_prediction() → auto-execute or queue
  → ActionExecutor (if not dry_run) → Gmail label write

## Core invariants / gotchas
- `auto_action=False` always; QueueManager owns execution decisions.
- `prediction.id` is NOT set by pipeline.process() — must be retrieved
  from DB after insert via get_predictions_for_email().
- `scoring_breakdown` is a JSON string (ScoreResult); missing breakdown
  skips QueueManager silently (logs warning, no error).
- `MAILMIND_DRY_RUN=1` prevents all real Gmail writes — safe for testing.
- DB path defaults to `~/.mailmind/mailmind.db`; override with
  MAILMIND_DB_PATH env var.
- Litestream replicates SQLite to S3 in prod; never write to DB file
  directly outside the Database class.

## Environment variables
MAILMIND_DB_PATH      SQLite DB path (default: ~/.mailmind/mailmind.db)
MAILMIND_APP_DIR      Config dir     (default: ~/.mailmind)
MAILMIND_POLL_SECONDS Poll interval  (default: 120)
MAILMIND_FETCH_MAX    Max emails/run (default: 50)
MAILMIND_DRY_RUN      '1' = skip real Gmail writes

## Deployment
- Fly.io: fly.toml + Dockerfile + fly-start.sh
- Render: render.yaml + render-start.sh
- Both use Litestream for SQLite replication to S3.
- macOS local: litestream-mac.sh + litestream-mac.yml

## Conventions
- Use Database class for all DB access.
- Use SafetyPolicy(dry_run=...) before any Gmail write.
- New pipeline stages go in mailmind/processing/.
- New Gmail actions go in mailmind/actions/.
- Tests live in mailmind/tests/.