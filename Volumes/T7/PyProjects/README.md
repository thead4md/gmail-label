# MailMind

Intelligent Gmail classification and labelling tool. Uses deterministic rules for email prioritisation with an optional DeepSeek LLM stage (Pass 7+) for advanced classification.

## Setup

```bash
# Clone the repository
git clone <repo-url> && cd mailmind

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy the environment template and fill in your values
cp .env.example .env
```

Edit `.env` with your Gmail address and DeepSeek API key (see below).

> **Note:** You'll need to complete the Gmail OAuth flow on first run:  
> `python -m mailmind.main auth`

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MAILMIND_USER_EMAIL` | — | Your primary Gmail address (required for the `directly_addressed` rule) |
| `MAILMIND_DB_PATH` | `~/.mailmind/mailmind.db` | Path to the SQLite database |
| `MAILMIND_APP_DIR` | `~/.mailmind` | Application configuration directory |
| `MAILMIND_POLL_SECONDS` | `120` | Poll interval in seconds (`--watch` mode) |
| `MAILMIND_FETCH_MAX` | `50` | Max emails to fetch per run |
| `MAILMIND_DRY_RUN` | `0` | Set to `1` to log actions without executing them |
| `DEEPSEEK_API_KEY` | — | DeepSeek API key (required to enable LLM classification) |
| `DEEPSEEK_MODEL` | `deepseek-chat` | DeepSeek model name |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | DeepSeek API base URL |
| `DEEPSEEK_MAX_CALLS_PER_RUN` | `10` | Max LLM API calls per pipeline run (cost control) |
| `DEEPSEEK_SKIP_THRESHOLD` | `70` | Emails with rules score >= this value skip the LLM stage |

---

## Usage

### One-shot classification

```bash
# Process the most recent unread emails from your INBOX
python -m mailmind.main run

# Dry-run mode (no Gmail label changes)
python -m mailmind.main run --dry-run

# Process only the last 20 emails
python -m mailmind.main run --fetch-max 20

# Combined: dry-run with limited fetch
python -m mailmind.main run --dry-run --fetch-max 20
```

### Continuous polling

```bash
# Watch mode — polls every 120 seconds (default)
python -m mailmind.main run --watch

# Watch mode with custom interval
python -m mailmind.main run --watch --poll-seconds 60
```

### LLM control

```bash
# Run without DeepSeek LLM classification (even if API key is set)
python -m mailmind.main run --no-llm
```

### Authentication

```bash
# Run the interactive OAuth flow manually
python -m mailmind.main auth
```

### Review Dashboard

```bash
# Launch the Streamlit dashboard for reviewing predictions and actions
streamlit run mailmind/review_dashboard.py
```

---

## DeepSeek LLM Setup (Pass 7+)

MailMind supports optional DeepSeek LLM classification as an additional stage in the processing pipeline. When enabled, each email is sent to DeepSeek's chat completions API for independent label prediction, which is then merged with the rules-based result.

### 1. Get an API key

1. Go to [platform.deepseek.com](https://platform.deepseek.com/) and sign up or log in.
2. Navigate to **API Keys** and create a new key.
3. Copy the key (it starts with `sk-...`).

### 2. Configure

```bash
# Edit your .env file
DEEPSEEK_API_KEY="sk-your-actual-key-here"
```

That's it! The LLM stage is automatically enabled when `DEEPSEEK_API_KEY` is set and non-empty.

### Cost guidance

- **Default budget:** 10 LLM API calls per pipeline run (`DEEPSEEK_MAX_CALLS_PER_RUN=10`).
- **Skip threshold:** Emails with a rules-based priority score >= 70 are **not** sent to the LLM (`DEEPSEEK_SKIP_THRESHOLD=70`). This saves costs on high-confidence emails that the rules engine handles well.
- **Text limit:** Only the first 500 characters of the email body are sent to the LLM.
- **Model:** Uses `deepseek-chat` by default, which is DeepSeek's most cost-effective model.

### Disabling per-run

```bash
# Skip the LLM stage for a specific run
python -m mailmind.main run --no-llm
```

### How it works

```
Email → Rules Engine → Priority Scorer → [LLM Stage] → Prediction
                                             │
                                    (skipped if score >= 70
                                     or budget exhausted)
```

The LLM result is merged into the prediction:
- If LLM confidence >= **0.90**, the LLM's label overrides the rules-based primary label.
- If LLM confidence < 0.90, the rules-based label is kept (but the LLM label is added as a secondary suggestion).
- The `pipeline_used` field is set to `"hybrid"` when the LLM contributes.
- All LLM results (label, confidence, reasoning) are recorded in the `scoring_breakdown` JSON.

---

## Architecture

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌────────────┐
│  Gmail   │ →  │  Fetcher │ →  │  Parser  │ →  │  Pipeline  │
│  API     │    │          │    │          │    │            │
└──────────┘    └──────────┘    └──────────┘    ├────────────┤
                                                │ Rules Eng. │
                                                │ Scorer     │
                                                │ LLM Client │
                                                └────────────┘
                                                      │
                                                      ▼
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌────────────┐
│  Review  │ ←  │  Queue   │ ←  │  Action  │ ←  │ Prediction │
│ Dashboard│    │  Manager │    │ Executor │    │ (SQLite)   │
└──────────┘    └──────────┘    └──────────┘    └────────────┘
```

---

## Development

### Running tests

```bash
# Run all tests
python -m pytest

# Run with coverage
python -m pytest --cov=mailmind

# Run specific test file
python -m pytest mailmind/tests/test_deepseek.py -v
```

### Project structure

```
mailmind/
├── actions/          # Action execution and safety policies
├── ingestion/        # Gmail API auth, fetch, parse
├── llm/              # DeepSeek LLM client (Pass 7+)
├── ml/               # Local ML model (future)
├── processing/       # Rules engine, scorer, pipeline
├── storage/          # Database, models, queries
├── tests/            # Test suite
├── config.py         # Configuration dataclass
├── main.py           # CLI entry point
└── review_dashboard.py  # Streamlit dashboard
```
