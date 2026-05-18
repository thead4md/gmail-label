"""MailMind — main entry point.

Pass 5: Live Gmail ingestion pipeline.

Usage:
    python -m mailmind.main run          # one-shot: fetch & classify
    python -m mailmind.main run --watch  # continuous: poll every N seconds
    python -m mailmind.main auth         # interactive OAuth flow only

Environment variables:
    MAILMIND_DB_PATH       Override SQLite DB path (default: ~/.mailmind/mailmind.db)
    MAILMIND_APP_DIR       Override config dir     (default: ~/.mailmind)
    MAILMIND_POLL_SECONDS  Poll interval in seconds (default: 120)
    MAILMIND_FETCH_MAX     Max emails per fetch run (default: 50)
    MAILMIND_DRY_RUN       Set to '1' to skip real Gmail label writes
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import click

from mailmind.ingestion.auth import authenticate, build_gmail_service
from mailmind.ingestion.fetcher import GmailFetcher
from mailmind.ingestion.parser import parse_message
from mailmind.processing.pipeline import run_pipeline
from mailmind.storage.database import Database
from mailmind.storage.models import Email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
LOG = logging.getLogger("mailmind")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> Database:
    db_path = os.environ.get("MAILMIND_DB_PATH", "~/.mailmind/mailmind.db")
    return Database(db_path)


def _process_message_id(message_id: str, fetcher: GmailFetcher, db: Database, dry_run: bool) -> None:
    """Fetch one message, persist it, run the classification pipeline."""
    try:
        raw = fetcher.get_message(message_id)
    except Exception as exc:
        LOG.warning("Failed to fetch message %s: %s", message_id, exc)
        return

    # Parse raw Gmail API response into an Email model
    try:
        email: Email = parse_message(raw)
    except Exception as exc:
        LOG.warning("Failed to parse message %s: %s", message_id, exc)
        return

    # Persist email (INSERT OR IGNORE — safe to re-run)
    try:
        db.insert_email(email)
    except Exception as exc:
        LOG.warning("Failed to insert email %s: %s", email.gmail_id, exc)
        return

    # Run the classification + action pipeline
    try:
        run_pipeline(email=email, db=db, dry_run=dry_run)
        LOG.info("Processed %s (subject: %s)", email.gmail_id, email.subject[:60] if email.subject else "—")
    except Exception as exc:
        LOG.error("Pipeline failed for %s: %s", email.gmail_id, exc, exc_info=True)


def _run_once(db: Database, dry_run: bool, fetch_max: int) -> None:
    """One-shot ingestion: authenticate → fetch unread INBOX → process each."""
    LOG.info("Authenticating with Gmail…")
    creds = authenticate()
    service = build_gmail_service(creds)
    fetcher = GmailFetcher(service)

    LOG.info("Fetching up to %d unread INBOX message IDs…", fetch_max)
    message_ids = fetcher.list_message_ids(label_ids=["INBOX", "UNREAD"], max_results=fetch_max)
    LOG.info("Found %d messages.", len(message_ids))

    for mid in message_ids:
        _process_message_id(mid, fetcher, db, dry_run)

    LOG.info("Run complete.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.group()
def cli() -> None:
    """MailMind — Gmail classification and labelling tool."""


@cli.command()
@click.option("--watch", is_flag=True, default=False, help="Poll continuously instead of one-shot.")
@click.option("--dry-run", is_flag=True, default=False, help="Classify but do not write labels to Gmail.")
@click.option("--fetch-max", default=None, type=int, help="Max emails to fetch per run.")
@click.option("--poll-seconds", default=None, type=int, help="Poll interval in seconds (--watch mode only).")
def run(
    watch: bool,
    dry_run: bool,
    fetch_max: Optional[int],
    poll_seconds: Optional[int],
) -> None:
    """Fetch recent Gmail messages, classify, and apply labels."""
    dry_run = dry_run or os.environ.get("MAILMIND_DRY_RUN", "") == "1"
    fetch_max = fetch_max or int(os.environ.get("MAILMIND_FETCH_MAX", "50"))
    poll_secs = poll_seconds or int(os.environ.get("MAILMIND_POLL_SECONDS", "120"))

    db = _get_db()

    if watch:
        LOG.info("Watch mode active — polling every %ds. Ctrl+C to stop.", poll_secs)
        while True:
            try:
                _run_once(db, dry_run=dry_run, fetch_max=fetch_max)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                LOG.error("Run failed: %s", exc, exc_info=True)
            LOG.info("Sleeping %ds…", poll_secs)
            time.sleep(poll_secs)
    else:
        _run_once(db, dry_run=dry_run, fetch_max=fetch_max)


@cli.command()
def auth() -> None:
    """Run the interactive OAuth2 flow and persist the token."""
    LOG.info("Starting OAuth2 flow…")
    creds = authenticate()
    LOG.info("Authentication successful. Token stored securely.")
    LOG.info("Scopes granted: %s", creds.scopes)


if __name__ == "__main__":
    cli()
