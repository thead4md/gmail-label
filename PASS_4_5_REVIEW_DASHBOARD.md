# MailMind Review Dashboard (Pass 4.5)

## Purpose

The review dashboard provides a **read‑only** Streamlit UI for inspecting predictions, actions, and sender reputations that MailMind has generated. It is designed for debugging, auditing, and monitoring the system without exposing raw email body text.

## How to Run

```bash
streamlit run mailmind/review_dashboard.py --server.address=127.0.0.1
```

The dashboard will be available at `http://127.0.0.1:8501`.

### Environment Variables

- `MAILMIND_DB_PATH` – path to the SQLite database (default: `~/.mailmind/mailmind.db`)

## Security Notes

- The dashboard binds **only to localhost** (`127.0.0.1`) by default. Do not expose it to the network.
- No email body text (`body_text`) is ever loaded or displayed.
- The dashboard is **read‑only**; it never modifies the database.

## Tabs

| Tab | Description |
|-----|-------------|
| **Overview** | Summary metrics (total emails, predictions, actions) and a table of the 10 most recent predictions. |
| **Prediction Detail** | Select a specific email (by `gmail_id`) to see all its predictions. |
| **Actions Log** | Table of the most recent action log entries (archive, star, etc.). |
| **Sender Reputation** | Table of all sender reputation scores, ordered by score descending. |

## Filters (Sidebar)

- **Date range** – filter by prediction/action creation date (default: last 7 days).
- **Pipeline used** – filter by `rules_only`, `ml`, `llm`, or show all.
- **Label** – filter by primary label (`INBOX`, `URGENT`, `FINANCE`, etc.) or show all.

## Dependencies

- Python ≥ 3.10
- Streamlit ≥ 1.35.0
- All other dependencies are already listed in `pyproject.toml` and `requirements.txt`.
