"""MailMind — main entry point.

Pass 5: Live Gmail ingestion pipeline.
Pass 7: Optional DeepSeek LLM classification stage.

Usage:
    python -m mailmind.main run          # one-shot: fetch & classify
    python -m mailmind.main run --watch  # continuous: poll every N seconds
    python -m mailmind.main auth         # interactive OAuth flow only

Environment variables:
    MAILMIND_DB_PATH           Override SQLite DB path (default: ~/.mailmind/mailmind.db)
    MAILMIND_APP_DIR           Override config dir     (default: ~/.mailmind)
    MAILMIND_POLL_SECONDS      Poll interval in seconds (default: 120)
    MAILMIND_FETCH_MAX         Max emails per fetch run (default: 50)
    MAILMIND_DRY_RUN           Set to '1' to skip real Gmail label writes
    MAILMIND_USER_EMAIL        User's primary email address (for scoring boosts)
    DEEPSEEK_API_KEY           DeepSeek API key (required for LLM stage)
    DEEPSEEK_MAX_CALLS_PER_RUN Max LLM calls per pipeline run (default: 10)
    DEEPSEEK_MODEL             DeepSeek model name (default: deepseek-chat)
    DEEPSEEK_BASE_URL          DeepSeek API base URL (default: https://api.deepseek.com/v1)
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import fields
from typing import Optional

import click

# Gracefully load .env if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; use environment variables directly


from mailmind.ingestion.auth import authenticate, build_gmail_service
from mailmind.ingestion.fetcher import GmailFetcher
from mailmind.ingestion.parser import parse_message
from mailmind.processing.pipeline import Pipeline
from mailmind.processing.rules import RulesEngine
from mailmind.processing.scorer import PriorityScorer, ScoreResult
from mailmind.actions.executor import ActionExecutor
from mailmind.actions.safety import SafetyPolicy
from mailmind.storage.database import Database
from mailmind.storage.models import Email
from mailmind.processing.queue_manager import QueueManager
import json

from mailmind.config import MailMindConfig
from mailmind.llm.deepseek import DeepSeekClient

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


def _build_components(
    db: Database, dry_run: bool, service,
    llm_client: Optional[DeepSeekClient] = None,
) -> tuple[Pipeline, QueueManager]:
    """Build Pipeline and QueueManager from default components.

    The ActionExecutor is created explicitly here and shared between
    Pipeline (for backward compatibility) and QueueManager (for
    auto-execute).

    Args:
        db: Database instance.
        dry_run: If True, actions are logged but not executed.
        service: Gmail API service object.
        llm_client: Optional DeepSeekClient for LLM classification stage.
    """
    config = MailMindConfig.from_env()
    user_email = os.environ.get("MAILMIND_USER_EMAIL", "")
    rules_engine = RulesEngine(user_email=user_email)
    scorer = PriorityScorer(user_email=user_email)
    safety = SafetyPolicy(dry_run=dry_run)
    if service is not None:
        executor = ActionExecutor(
            service=service,
            db=db,
            safety_policy=safety,
        )
    else:
        executor = None
    pipeline = Pipeline(
        db=db,
        rules_engine=rules_engine,
        scorer=scorer,
        executor=executor,
        safety_policy=safety,
        llm_client=llm_client,
        llm_skip_threshold=config.llm_skip_threshold,
        llm_max_calls_per_run=config.llm_max_calls_per_run,
    )
    queue_manager = QueueManager(executor=executor)
    return pipeline, queue_manager


def _process_message_id(
    message_id: str,
    fetcher: GmailFetcher,
    pipeline: Pipeline,
    queue_manager: QueueManager,
    reclassify: bool = False,
) -> None:
    """Fetch one message, persist it, run the classification pipeline.

    The QueueManager handles action execution instead of auto_action,
    so the pipeline runs with auto_action=False.

    Args:
        reclassify: If True, re-run an already-classified email. Default False
            skips emails that already have a prediction — re-classifying an
            immutable email every poll cycle just wastes a Gmail fetch and an
            LLM call. The Gmail message id IS our gmail_id, so this check runs
            before the expensive get_message() call.
    """
    if not reclassify and pipeline.db.has_prediction(message_id):
        LOG.debug("Skipping %s — already classified.", message_id)
        return

    try:
        raw = fetcher.get_message(message_id)
    except Exception as exc:
        LOG.warning("Failed to fetch message %s: %s", message_id, exc)
        return

    try:
        email: Email = parse_message(raw)
    except Exception as exc:
        LOG.warning("Failed to parse message %s: %s", message_id, exc)
        return

    try:
        pipeline.db.insert_email(email)
    except Exception as exc:
        LOG.warning("Failed to insert email %s: %s", email.gmail_id, exc)
        return

    try:
        # auto_action=False — QueueManager handles execution decisions
        prediction = pipeline.process(email=email, auto_action=False)
        LOG.info(
            "Processed %s | label=%s score=%.2f",
            email.gmail_id,
            prediction.primary_label,
            prediction.priority_score or 0.0,
        )

        # pipeline.process() now sets prediction.id from save_prediction().
        # This fallback only fires if persistence failed mid-run.
        if not getattr(prediction, "id", None):
            rows = pipeline.db.get_predictions_for_email(email.gmail_id)
            if rows:
                prediction.id = rows[0]["id"]

        # Feed prediction into the QueueManager if we have a scoring breakdown
        if prediction.scoring_breakdown:
            try:
                score_data = json.loads(prediction.scoring_breakdown)
                valid_fields = {f.name for f in fields(ScoreResult)}
                score_result = ScoreResult(**{k: v for k, v in score_data.items() if k in valid_fields})
                status = queue_manager.enqueue_from_prediction(
                    pipeline.db, email, score_result, prediction,
                )
                LOG.info(
                    "QueueManager for %s: status=%s, action=%s",
                    email.gmail_id,
                    status,
                    prediction.action_suggested,
                )
            except (json.JSONDecodeError, TypeError) as exc:
                LOG.error(
                    "Failed to parse scoring_breakdown for %s: %s",
                    email.gmail_id,
                    exc,
                )
        else:
            LOG.warning(
                "No scoring_breakdown for %s; skipping queue manager.",
                email.gmail_id,
            )

    except Exception as exc:
        LOG.error("Pipeline failed for %s: %s", email.gmail_id, exc, exc_info=True)


def _run_once(db: Database, dry_run: bool, fetch_max: int, no_llm: bool = False) -> None:
    """One-shot: authenticate → fetch unread INBOX → process each message.

    Args:
        db: Database instance.
        dry_run: If True, actions are logged but not executed.
        fetch_max: Maximum number of messages to fetch per run.
        no_llm: If True, skip LLM classification even if API key is set.
    """
    LOG.info("Authenticating with Gmail…")
    creds = authenticate()
    service = build_gmail_service(creds)
    fetcher = GmailFetcher(service)

    # Initialize LLM client (Pass 7+)
    config = MailMindConfig.from_env()
    llm_client = None
    if config.llm_enabled and not no_llm:
        llm_client = DeepSeekClient(config)
        LOG.info("LLM stage: enabled (%s)", config.deepseek_model)
    else:
        LOG.info("LLM stage: disabled")

    pipeline, queue_manager = _build_components(db, dry_run, service, llm_client=llm_client)

    # Fetch only UNREAD INBOX messages. Gmail treats UNREAD as a label, so
    # passing both label ids returns their intersection (unread in inbox).
    # Read mail drops out of the set, so the loop stops re-scanning it.
    LOG.info("Fetching up to %d unread INBOX message IDs…", fetch_max)
    message_ids = fetcher.list_message_ids(label_ids=["INBOX", "UNREAD"], max_results=fetch_max)
    LOG.info("Found %d messages.", len(message_ids))

    for mid in message_ids:
        _process_message_id(mid, fetcher, pipeline, queue_manager)

    LOG.info("Run complete.")


def _maybe_prune(db: Database, retention_days: int, interval_seconds: int = 86400) -> None:
    """Run a retention sweep at most once per ``interval_seconds``.

    Tracks the last run in system_state so the watch loop self-maintains
    without growing the local cache (or the Litestream S3 replica) forever.
    Failures here must never take down the watch loop.
    """
    try:
        last = db.get_state("last_prune_ts")
        now = int(time.time())
        if last is not None and now - int(last) < interval_seconds:
            return
        counts = db.prune_old_data(retention_days)
        db.vacuum()
        db.set_state("last_prune_ts", str(now))
        LOG.info("Retention sweep (keep %dd): %s", retention_days, counts)
    except Exception as exc:
        LOG.error("Retention sweep failed: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.group()
@click.option("--log-level", default="INFO",
              type=click.Choice(["DEBUG","INFO","WARNING","ERROR"], case_sensitive=False),
              help="Set logging verbosity.")
@click.pass_context
def cli(ctx: click.Context, log_level: str) -> None:
    """MailMind — Gmail classification and labelling tool."""
    logging.getLogger().setLevel(log_level.upper())


@cli.command()
@click.option("--watch", is_flag=True, default=False, help="Poll continuously instead of one-shot.")
@click.option("--dry-run", is_flag=True, default=False, help="Classify but do not write labels to Gmail.")
@click.option("--fetch-max", default=None, type=int, help="Max emails to fetch per run.")
@click.option("--poll-seconds", default=None, type=int, help="Poll interval in seconds (--watch mode only).")
@click.option("--no-llm", is_flag=True, default=False,
              help="Disable DeepSeek LLM classification stage for this run.")
def run(
    watch: bool,
    dry_run: bool,
    fetch_max: Optional[int],
    poll_seconds: Optional[int],
    no_llm: bool,
) -> None:
    """Fetch recent Gmail messages, classify, and apply labels."""
    user_email = os.environ.get("MAILMIND_USER_EMAIL")
    if not user_email:
        LOG.warning("MAILMIND_USER_EMAIL is not set. "
                     "The 'directly_addressed' rule will be skipped.")

    dry_run = dry_run or os.environ.get("MAILMIND_DRY_RUN", "") == "1"
    fetch_max = fetch_max or int(os.environ.get("MAILMIND_FETCH_MAX", "50"))
    poll_secs = poll_seconds or int(os.environ.get("MAILMIND_POLL_SECONDS", "120"))
    retention_days = int(os.environ.get("MAILMIND_RETENTION_DAYS", "90"))

    db = _get_db()

    if watch:
        LOG.info("Watch mode active — polling every %ds. Ctrl+C to stop.", poll_secs)
        while True:
            try:
                _run_once(db, dry_run=dry_run, fetch_max=fetch_max, no_llm=no_llm)
                _maybe_prune(db, retention_days)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                LOG.error("Run failed: %s", exc, exc_info=True)
            LOG.info("Sleeping %ds…", poll_secs)
            time.sleep(poll_secs)
    else:
        _run_once(db, dry_run=dry_run, fetch_max=fetch_max, no_llm=no_llm)


@cli.command()
@click.option("--retention-days", default=None, type=int,
              help="Delete local cache older than N days (default: MAILMIND_RETENTION_DAYS or 90).")
@click.option("--no-vacuum", is_flag=True, default=False,
              help="Skip the VACUUM step (faster, no disk reclaim).")
def prune(retention_days: Optional[int], no_vacuum: bool) -> None:
    """Prune old locally-cached emails/predictions and reclaim disk space.

    Never touches Gmail — only the local SQLite cache. Pending review items
    are always preserved.
    """
    days = retention_days or int(os.environ.get("MAILMIND_RETENTION_DAYS", "90"))
    db = _get_db()
    counts = db.prune_old_data(days)
    if not no_vacuum:
        db.vacuum()
    click.echo(f"Pruned (retention={days}d): {counts}")


@cli.command()
def auth() -> None:
    """Run the interactive OAuth2 flow and persist the token."""
    LOG.info("Starting OAuth2 flow…")
    creds = authenticate()
    LOG.info("Authentication successful. Token stored securely.")
    LOG.info("Scopes granted: %s", creds.scopes)


if __name__ == "__main__":
    cli()
