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


from mailmind.ingestion.auth import authenticate, build_gmail_service, load_stored_credentials
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

    # Revive the ML tier: load the trained classifier ONCE per run (not per
    # email) and wire a ClassifierRouter so rules->ML->LLM tiering actually
    # happens. When ML is confident enough, the paid DeepSeek call is skipped.
    # If no model file is on disk, router still works (rules + LLM only).
    classifier_router = _build_classifier_router(rules_engine)

    pipeline = Pipeline(
        db=db,
        rules_engine=rules_engine,
        scorer=scorer,
        executor=executor,
        safety_policy=safety,
        llm_client=llm_client,
        llm_skip_threshold=config.llm_skip_threshold,
        llm_max_calls_per_run=config.llm_max_calls_per_run,
        classifier_router=classifier_router,
    )
    queue_manager = QueueManager(executor=executor)
    return pipeline, queue_manager


# Hot-reload cache for the ML model. The watch loop rebuilds the router
# every cycle; without this we'd re-read joblib from disk on every cycle even
# when nothing changed. Keyed by (path, mtime) so a retrain (which rewrites
# the file with a fresh mtime) is picked up automatically — no process
# restart needed.
_MODEL_CACHE: dict = {"path": None, "mtime": None, "classifier": None}


def _load_ml_classifier_cached() -> Optional["MLClassifier"]:
    """Load the ML classifier, reloading only when the model file's mtime changes.

    Returns the cached MLClassifier when the file is unchanged since last
    load, a freshly-loaded one when the file changed, or None when no model
    file exists or loading failed.
    """
    from mailmind.ml.model import MLClassifier
    try:
        clf = MLClassifier()
        model_path = clf.get_model_path()
    except Exception as exc:
        LOG.warning("ML classifier init failed (%s) — running without ML tier.", exc)
        return None

    if not model_path.exists():
        if _MODEL_CACHE["classifier"] is not None:
            LOG.info("ML model file disappeared — disabling ML tier.")
        _MODEL_CACHE.update({"path": None, "mtime": None, "classifier": None})
        return None

    try:
        mtime = model_path.stat().st_mtime
    except OSError:
        return _MODEL_CACHE.get("classifier")

    cached = _MODEL_CACHE.get("classifier")
    if (
        cached is not None
        and _MODEL_CACHE.get("path") == str(model_path)
        and _MODEL_CACHE.get("mtime") == mtime
    ):
        return cached

    try:
        loaded = clf.load()
    except Exception as exc:
        LOG.warning("ML model load failed (%s) — running without ML tier.", exc)
        return None
    if not loaded:
        _MODEL_CACHE.update({"path": str(model_path), "mtime": mtime, "classifier": None})
        return None

    _MODEL_CACHE.update({"path": str(model_path), "mtime": mtime, "classifier": clf})
    LOG.info(
        "ML model %s (classes=%s, samples=%s).",
        "loaded" if cached is None else "reloaded",
        clf._metadata.class_names if clf._metadata else "?",
        clf._metadata.num_samples if clf._metadata else "?",
    )
    return clf


# Minimum measured hold-out accuracy before the local ML model is trusted to
# classify at inference. An unvalidated model (accuracy=None) is never used.
# Set to 0.65 for the (now ~9-class) content taxonomy: well above the majority
# baseline (~0.25), and the per-prediction ml_threshold (0.65) plus the LLM
# fallback still catch low-confidence ML predictions, so a 0.65-accurate model
# is a safe, free first tier rather than the rules echo. Rises naturally as
# corrections accumulate.
ML_MIN_ACCURACY = 0.65


def _build_llm_client(config: "MailMindConfig", no_llm: bool = False):
    """Build the content-classification LLM client, honouring the chosen provider.

    Provider is selected by the LLM_PROVIDER env var (openai | deepseek | auto):
      - "openai"   → OpenAI (requires OPENAI_API_KEY); raises if key missing.
      - "deepseek" → DeepSeek (requires DEEPSEEK_API_KEY).
      - "auto" (default) → OpenAI when OPENAI_API_KEY is set, else DeepSeek.

    Returns a client exposing classify_email(email) -> LLMResult (the unified
    Protocol), or None when no provider is available / no_llm is set. Both
    DeepSeekClient and the OpenAI adapter conform to the same interface, so the
    Pipeline does not care which one it gets.
    """
    if no_llm:
        return None
    provider = os.environ.get("LLM_PROVIDER", "auto").strip().lower()
    has_openai = bool(config.openai_api_key)
    use_openai = provider == "openai" or (provider == "auto" and has_openai)

    if use_openai:
        if not has_openai:
            LOG.warning("LLM_PROVIDER=openai but OPENAI_API_KEY is unset — LLM disabled.")
            return None
        from mailmind.ml.llm_classifier import LLMClassifier, OpenAIAdapter
        client = OpenAIAdapter(LLMClassifier(
            api_key=config.openai_api_key, model=config.openai_model,
        ))
        LOG.info("LLM stage: enabled via OpenAI (%s)", config.openai_model)
        return client

    if config.llm_enabled:
        LOG.info("LLM stage: enabled via DeepSeek (%s)", config.deepseek_model)
        return DeepSeekClient(config)

    LOG.info("LLM stage: disabled (no provider configured)")
    return None


def _build_classifier_router(rules_engine: RulesEngine) -> Optional["ClassifierRouter"]:
    """Build the router with the (mtime-cached) ML classifier.

    Returns a router even when the model isn't loaded so the rules tier
    still runs; the pipeline degrades gracefully (rules + LLM only).
    LLM is disabled on the router itself — the LLM client is wired separately on
    the Pipeline; we don't want two LLM paths firing in parallel.

    The ML tier is only attached when the loaded model has a measured accuracy
    >= ML_MIN_ACCURACY. An unvalidated model (accuracy=None) is loaded for
    metadata/visibility but not used for inference, so ambiguous mail flows past
    it to the LLM rather than being intercepted by a rules-echoing model.
    """
    from mailmind.ml.classifier_router import ClassifierRouter
    classifier = _load_ml_classifier_cached()
    ml_model = classifier
    if classifier is not None:
        meta = getattr(classifier, "_metadata", None)
        acc = getattr(meta, "accuracy", None) if meta else None
        if acc is None or acc < ML_MIN_ACCURACY:
            LOG.info(
                "ML tier disabled for inference: model accuracy=%s < %.2f "
                "(unvalidated). Routing ambiguous mail to the LLM instead.",
                acc, ML_MIN_ACCURACY,
            )
            ml_model = None
    return ClassifierRouter(
        rules_engine=rules_engine,
        ml_model=ml_model,
        llm_classifier=None,
        llm_enabled=False,
    )


def _process_message_id(
    message_id: str,
    fetcher: GmailFetcher,
    pipeline: Pipeline,
    queue_manager: QueueManager,
    reclassify: bool = False,
    account: Optional[str] = None,
    classify_only: bool = False,
    prefetched_raw: Optional[dict] = None,
    label_map: Optional[dict] = None,
    truth_include: Optional[list] = None,
    truth_exclude: Optional[list] = None,
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
        classify_only: If True, store the prediction (label + channel) but skip
            the QueueManager entirely. Used by the backfill command so a large
            historical sweep populates training data WITHOUT flooding the review
            queue or triggering any action execution.
    """
    if not reclassify and pipeline.db.has_prediction(message_id):
        LOG.debug("Skipping %s — already classified.", message_id)
        return

    if prefetched_raw is not None:
        raw = prefetched_raw
    else:
        try:
            raw = fetcher.get_message(message_id)
        except Exception as exc:
            LOG.warning("Failed to fetch message %s: %s", message_id, exc)
            return

    try:
        email: Email = parse_message(raw)
        email.account = account
    except Exception as exc:
        LOG.warning("Failed to parse message %s: %s", message_id, exc)
        return

    try:
        pipeline.db.insert_email(email)
    except Exception as exc:
        LOG.warning("Failed to insert email %s: %s", email.gmail_id, exc)
        return

    # Capture the email's real Gmail labels as training signal (Phase 1a, automatic).
    if label_map is not None:
        try:
            from mailmind.intelligence.labels import resolve_truth_labels
            names = resolve_truth_labels(
                email.labels or [], label_map,
                truth_include or [], truth_exclude or [],
            )
            pipeline.db.set_email_user_labels(email.gmail_id, ",".join(names))
        except Exception as exc:
            LOG.debug("user_labels capture failed for %s: %s", email.gmail_id, exc)

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

        # Backfill path: store the prediction (label + channel) for training,
        # but never enqueue — a 3-month sweep must not flood the review queue.
        if classify_only:
            return

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


def _run_once(
    db: Database,
    dry_run: bool,
    fetch_max: int,
    no_llm: bool = False,
    account: Optional[str] = None,
    auth_account: Optional[str] = None,
    allow_interactive: bool = True,
) -> None:
    """One-shot for a single mailbox: authenticate → fetch unread → process.

    Args:
        account: Data label this mailbox's rows are tagged with (None = legacy
            single-account, leaves rows untagged for back-compat).
        auth_account: Token identity to load (None = legacy token storage; the
            primary account reuses the legacy token so existing deployments keep
            working unchanged).
        allow_interactive: If True, fall back to the interactive OAuth flow when
            no stored token exists. Set False for secondary mailboxes so a
            not-yet-connected account is skipped instead of blocking.
    """
    label = account or "primary"
    LOG.info("Authenticating mailbox %s…", label)
    creds = load_stored_credentials(auth_account)
    if creds is None:
        if allow_interactive:
            creds = authenticate(account=auth_account)
        else:
            LOG.warning(
                "No stored credentials for mailbox %s — skipping. Connect it by "
                "setting the GMAIL_TOKEN_<SLUG> secret or placing its token file.",
                label,
            )
            return
    service = build_gmail_service(creds)
    fetcher = GmailFetcher(service)

    # Initialize LLM client (Pass 7+) — provider selected by LLM_PROVIDER.
    config = MailMindConfig.from_env()
    llm_client = _build_llm_client(config, no_llm=no_llm)

    pipeline, queue_manager = _build_components(db, dry_run, service, llm_client=llm_client)

    # Refresh the label-id→name map once per cycle so new mail can be tagged
    # with the user's real labels (truth signal). Cheap: one labels.list call.
    from mailmind.intelligence.labels import truth_label_policy
    label_map = fetcher.list_label_map()
    if label_map:
        db.upsert_label_map(account, label_map)
    truth_include, truth_exclude = truth_label_policy()

    # Fetch only UNREAD INBOX messages. Gmail treats UNREAD as a label, so
    # passing both label ids returns their intersection (unread in inbox).
    # Read mail drops out of the set, so the loop stops re-scanning it.
    LOG.info("[%s] Fetching up to %d unread INBOX message IDs…", label, fetch_max)
    message_ids = fetcher.list_message_ids(label_ids=["INBOX", "UNREAD"], max_results=fetch_max)
    LOG.info("[%s] Found %d messages.", label, len(message_ids))

    # Skip already-classified ids before the network round-trip, then batch-fetch
    # the rest (one HTTP request per 100 instead of one per message).
    todo = [m for m in message_ids if not pipeline.db.has_prediction(m)]
    LOG.info("[%s] %d new of %d to fetch.", label, len(todo), len(message_ids))
    raw_by_id = fetcher.batch_get_messages(todo)
    for mid in todo:
        raw = raw_by_id.get(mid)
        if raw is None:
            continue
        _process_message_id(mid, fetcher, pipeline, queue_manager,
                            account=account, prefetched_raw=raw,
                            label_map=label_map, truth_include=truth_include,
                            truth_exclude=truth_exclude)

    LOG.info("[%s] Run complete.", label)


def _run_all_accounts(db: Database, dry_run: bool, fetch_max: int, no_llm: bool = False) -> None:
    """Run one cycle across every configured mailbox.

    With no MAILMIND_ACCOUNTS configured, behaves exactly like the legacy
    single-account run. Otherwise iterates each account: the first (primary)
    reuses the legacy token and may auth interactively; secondary mailboxes
    load their own stored token and are skipped if not yet connected.
    A failure on one account never aborts the others.
    """
    accounts = MailMindConfig.load_accounts()
    if not accounts:
        _run_once(db, dry_run=dry_run, fetch_max=fetch_max, no_llm=no_llm)
        return

    for i, acct in enumerate(accounts):
        is_primary = i == 0
        try:
            _run_once(
                db,
                dry_run=dry_run,
                fetch_max=fetch_max,
                no_llm=no_llm,
                account=acct,
                auth_account=None if is_primary else acct,
                allow_interactive=is_primary,
            )
        except Exception as exc:
            LOG.error("Run failed for mailbox %s: %s", acct, exc, exc_info=True)


def _maybe_retrain(
    db: Database,
    interval_seconds: int = 7 * 86400,
    corrections_threshold: int = 5,
) -> None:
    """Retrain the ML model when either trigger fires:

      1. CADENCE: it's been >= interval_seconds since the last retrain.
      2. CORRECTIONS: the user has logged >= corrections_threshold new
         corrections since the last retrain.

    Tracked in system_state. The next watch cycle picks up the new model
    automatically via the mtime hot-reload path. Failures here MUST never
    take down the watch loop.
    """
    try:
        last_train_ts = int(db.get_state("last_train_ts") or 0)
        last_train_corrections = int(db.get_state("last_train_corrections_count") or 0)

        now = int(time.time())
        total_corrections = db.execute_sql(
            "SELECT COUNT(*) AS c FROM user_corrections"
        ).fetchone()["c"]
        new_corrections = max(0, total_corrections - last_train_corrections)
        age = now - last_train_ts

        cadence_due = last_train_ts == 0 or age >= interval_seconds
        corrections_due = new_corrections >= corrections_threshold

        if not (cadence_due or corrections_due):
            return

        trigger = "cadence" if cadence_due else "corrections"
        LOG.info(
            "Auto-retrain triggered (%s: age=%ds, new_corrections=%d).",
            trigger, age, new_corrections,
        )
        from mailmind.ml.train import train_model_from_db
        classifier = train_model_from_db(db, min_accuracy=ML_MIN_ACCURACY)
        if classifier is None:
            # No promotion: either insufficient data (cold start — retry next
            # cycle, state left unset) or the model didn't clear the accuracy
            # floor. The previous live model stays in place.
            LOG.info(
                "Auto-retrain produced no promoted model "
                "(insufficient data or hold-out accuracy < %.2f floor).",
                ML_MIN_ACCURACY,
            )
            return

        db.set_state("last_train_ts", str(now))
        db.set_state("last_train_corrections_count", str(total_corrections))
        LOG.info(
            "Auto-retrain complete and promoted (samples=%d, accuracy=%s).",
            classifier.metadata.num_samples if classifier.metadata else -1,
            classifier.metadata.accuracy if classifier.metadata else None,
        )
    except Exception as exc:
        LOG.error("Auto-retrain failed: %s", exc, exc_info=True)


# Heartbeat: the watch loop stamps system_state every cycle so the dashboard
# (and any external monitor) can detect a silent hang. We deliberately bump
# this AFTER ingest+retrain+prune so a stuck stage shows as a stale heartbeat
# instead of falsely-fresh.
HEARTBEAT_KEY = "last_heartbeat_ts"


def _record_heartbeat(db: Database) -> None:
    """Stamp the current time as the last successful watch-loop cycle.

    Failure here must never take down the watch loop — a heartbeat write
    error would defeat the whole point.
    """
    try:
        db.set_state(HEARTBEAT_KEY, str(int(time.time())))
    except Exception as exc:
        LOG.warning("Heartbeat write failed: %s", exc)


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


def _maybe_refresh_labels(db: Database, interval_seconds: int = 86400) -> None:
    """Re-resolve user_labels across all mail at most once per interval.
    Catches labels the user changed directly in Gmail on older messages.
    Tracked in system_state; failures never take down the watch loop.
    """
    try:
        last = db.get_state("last_label_refresh_ts")
        now = int(time.time())
        if last is not None and now - int(last) < interval_seconds:
            return
        accounts = MailMindConfig.load_accounts()
        for acct in (accounts or [None]):
            auth_account = _auth_account_for(acct) if acct else None
            is_primary = (acct is None) or (accounts and acct == accounts[0])
            try:
                _refresh_labels_one_account(db, acct, auth_account, bool(is_primary))
            except Exception as exc:
                LOG.warning("label refresh failed for %s: %s", acct or "primary", exc)
        db.set_state("last_label_refresh_ts", str(now))
    except Exception as exc:
        LOG.warning("label refresh skipped: %s", exc)


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
                _run_all_accounts(db, dry_run=dry_run, fetch_max=fetch_max, no_llm=no_llm)
                _maybe_retrain(db)
                _maybe_prune(db, retention_days)
                _maybe_refresh_labels(db)
                _record_heartbeat(db)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                LOG.error("Run failed: %s", exc, exc_info=True)
            LOG.info("Sleeping %ds…", poll_secs)
            time.sleep(poll_secs)
    else:
        _run_all_accounts(db, dry_run=dry_run, fetch_max=fetch_max, no_llm=no_llm)


@cli.command()
@click.option("--days", default=1, type=int, help="Look-back window (default: 1).")
@click.option("--account", default=None, help="Scope to a single mailbox.")
def digest(days: int, account: Optional[str]) -> None:
    """Print a summary of what MailMind has done over the last N days."""
    from mailmind.storage.queries import build_digest as _build_digest
    db = _get_db()
    since_ts = int(time.time()) - days * 86400
    d = _build_digest(db, since_ts=since_ts, account=account)
    scope = f" ({account})" if account else ""
    click.echo(f"MailMind digest — last {days}d{scope}")
    click.echo(f"  Classified:           {d['classified']}")
    click.echo(f"  Executed:             {d['executed']}")
    click.echo(f"  Execute failed:       {d['execute_failed']}")
    click.echo(f"  Pending review:       {d['queued']} "
               f"(reply needed: {d['pending_reply_needed']})")
    click.echo(f"  Corrections logged:   {d['corrections']}")
    if d["top_labels"]:
        click.echo("  Top labels:")
        for row in d["top_labels"]:
            click.echo(f"    - {row['label']}: {row['count']}")


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


def _backfill_one_account(
    db: Database,
    months: int,
    max_emails: int,
    no_llm: bool,
    reclassify: bool,
    account: Optional[str],
    auth_account: Optional[str],
    allow_interactive: bool,
    max_llm_calls: int = 5000,
) -> int:
    """Classify-only sweep of one mailbox over the last `months` months.

    Fetches INBOX mail in the date window (read + unread), classifies each,
    and stores the prediction (label + channel). Never enqueues actions and
    never writes to Gmail. Returns the number of messages processed.
    """
    label = account or "primary"
    creds = load_stored_credentials(auth_account)
    if creds is None:
        if allow_interactive:
            creds = authenticate(account=auth_account)
        else:
            LOG.warning("No stored credentials for mailbox %s — skipping.", label)
            return 0
    service = build_gmail_service(creds)
    fetcher = GmailFetcher(service)

    config = MailMindConfig.from_env()
    llm_client = _build_llm_client(config, no_llm=no_llm)
    LOG.info("[%s] Backfill LLM stage: %s", label,
             "enabled" if llm_client else "disabled (cost control)")

    # dry_run=True: a backfill must never mutate Gmail.
    pipeline, queue_manager = _build_components(db, dry_run=True, service=service,
                                                llm_client=llm_client)
    # A deliberate historical sweep must not be throttled by the live per-run LLM
    # guard (default 10). Raise the cap so every fallback email can be labelled.
    if llm_client is not None:
        pipeline.llm_max_calls_per_run = max_llm_calls

    query = f"newer_than:{months}m"
    LOG.info("[%s] Backfill: listing INBOX message IDs for q=%r (max %d)…",
             label, query, max_emails)
    message_ids = fetcher.list_message_ids(
        label_ids=["INBOX"], max_results=max_emails, query=query,
    )
    LOG.info("[%s] Backfill: %d messages in window.", label, len(message_ids))

    if reclassify:
        todo = list(message_ids)
    else:
        todo = [m for m in message_ids if not pipeline.db.has_prediction(m)]
    LOG.info("[%s] Backfill: %d new of %d to fetch.", label, len(todo), len(message_ids))
    raw_by_id = fetcher.batch_get_messages(todo)
    processed = 0
    for i, mid in enumerate(todo, 1):
        raw = raw_by_id.get(mid)
        if raw is None:
            continue
        _process_message_id(
            mid, fetcher, pipeline, queue_manager,
            reclassify=reclassify, account=account, classify_only=True,
            prefetched_raw=raw,
        )
        processed += 1
        if i % 100 == 0:
            LOG.info("[%s] Backfill progress: %d/%d", label, i, len(todo))

    # Surface silent truncation: if the LLM cap was reached, some fallback
    # emails were classified WITHOUT the LLM and won't carry an llm_label.
    if llm_client is not None and getattr(pipeline, "_llm_calls_this_run", 0) >= max_llm_calls:
        LOG.warning(
            "[%s] LLM call cap (%d) reached during backfill — some fallback "
            "emails were not LLM-labelled. Re-run with a higher --max-llm-calls "
            "to cover the rest.", label, max_llm_calls,
        )
    LOG.info("[%s] Backfill complete: %d processed (%d LLM calls).",
             label, processed, getattr(pipeline, "_llm_calls_this_run", 0))
    return processed


@cli.command()
@click.option("--months", default=3, type=int, help="Look-back window in months (default: 3).")
@click.option("--max-emails", default=2000, type=int,
              help="Max messages to fetch per mailbox (default: 2000).")
@click.option("--account", default=None, help="Scope to a single mailbox (default: all).")
@click.option("--with-llm", is_flag=True, default=False,
              help="Use the LLM tier (OpenAI/DeepSeek per LLM_PROVIDER; costs money).")
@click.option("--max-llm-calls", default=5000, type=int,
              help="Max LLM calls for this backfill (cost cap; default 5000).")
@click.option("--reclassify", is_flag=True, default=False,
              help="Re-classify emails that already have a prediction.")
def backfill(months: int, max_emails: int, account: Optional[str],
             with_llm: bool, max_llm_calls: int, reclassify: bool) -> None:
    """Classify the last N months of INBOX mail to seed categories + ML training.

    Classify-only: stores label + channel for every email in the window but
    never enqueues actions and never writes to Gmail. Idempotent — already
    classified emails are skipped unless --reclassify is given. After it runs,
    retrain with: python -m mailmind.scripts.train_ml_model
    """
    db = _get_db()
    no_llm = not with_llm
    accounts = MailMindConfig.load_accounts()

    if account:
        targets = [account]
    elif accounts:
        targets = accounts
    else:
        targets = [None]  # legacy single-account

    total = 0
    for acct in targets:
        auth_account = _auth_account_for(acct) if acct else None
        # Primary (or legacy) may use interactive auth; secondaries are skipped if unconnected.
        is_primary = (acct is None) or (accounts and acct == accounts[0])
        total += _backfill_one_account(
            db, months=months, max_emails=max_emails, no_llm=no_llm,
            reclassify=reclassify, account=acct, auth_account=auth_account,
            allow_interactive=bool(is_primary), max_llm_calls=max_llm_calls,
        )

    click.echo(f"Backfill complete: {total} message(s) classified across "
               f"{len(targets)} mailbox(es), window={months}m, llm={'on' if with_llm else 'off'}.")
    click.echo("Next: python -m mailmind.scripts.train_ml_model  (to retrain on the new data)")


def _friendly_label(primary_label: str) -> str:
    """'MASS_EMAIL' -> 'Mass Email', 'WORK' -> 'Work'."""
    return (primary_label or "").replace("_", " ").title()


def _apply_labels_one_account(
    db: Database,
    since_ts: int,
    account: Optional[str],
    auth_account: Optional[str],
    allow_interactive: bool,
    prefix: str,
    execute: bool,
) -> dict:
    """Stamp predicted category labels onto Gmail for one mailbox.

    Dry-run (execute=False) only counts; nothing is written. Returns a dict of
    {label_name: count} plus an 'applied' total.
    """
    from mailmind.storage.queries import get_labeled_predictions

    label_lbl = account or "primary"
    preds = get_labeled_predictions(db, since_ts=since_ts, account=account)
    if not preds:
        LOG.info("[%s] apply-labels: no classified emails in window.", label_lbl)
        return {"applied": 0}

    # Group message ids by destination Gmail label name.
    by_label: dict = {}
    for p in preds:
        name = f"{prefix}{_friendly_label(p['primary_label'])}"
        by_label.setdefault(name, []).append(p["email_gmail_id"])

    counts = {name: len(ids) for name, ids in by_label.items()}
    if not execute:
        return {**counts, "applied": 0}

    # Live path: authenticate, ensure each label exists, batch-add.
    creds = load_stored_credentials(auth_account)
    if creds is None:
        if allow_interactive:
            creds = authenticate(account=auth_account)
        else:
            LOG.warning("[%s] apply-labels: no credentials — skipping.", label_lbl)
            return {"applied": 0}
    fetcher = GmailFetcher(build_gmail_service(creds))

    applied = 0
    for name, ids in by_label.items():
        label_id = fetcher.ensure_label(name)
        if not label_id:
            LOG.warning("[%s] apply-labels: could not ensure label '%s'.", label_lbl, name)
            continue
        applied += fetcher.batch_add_label(ids, label_id)
        LOG.info("[%s] apply-labels: %s -> %d messages.", label_lbl, name, len(ids))
    return {**counts, "applied": applied}


@cli.command(name="apply-labels")
@click.option("--months", default=3, type=int, help="Look-back window in months (default: 3).")
@click.option("--account", default=None, help="Scope to a single mailbox (default: all).")
@click.option("--prefix", default="MailMind/",
              help="Gmail label prefix (default: 'MailMind/' — nests under one parent).")
@click.option("--execute", is_flag=True, default=False,
              help="Actually write labels to Gmail. Omit for a dry-run preview.")
def apply_labels(months: int, account: Optional[str], prefix: str, execute: bool) -> None:
    """Stamp MailMind's predicted category onto Gmail messages in bulk.

    DRY-RUN BY DEFAULT: prints how many emails would get each label and writes
    nothing. Re-run with --execute to apply. Idempotent (re-adding an existing
    label is a no-op) and additive only (never archives/deletes).
    """
    db = _get_db()
    since_ts = int(time.time()) - months * 30 * 86400
    accounts = MailMindConfig.load_accounts()
    targets = [account] if account else (accounts or [None])

    grand_total = 0
    grand_applied = 0
    for acct in targets:
        auth_account = _auth_account_for(acct) if acct else None
        is_primary = (acct is None) or (accounts and acct == accounts[0])
        res = _apply_labels_one_account(
            db, since_ts=since_ts, account=acct, auth_account=auth_account,
            allow_interactive=bool(is_primary), prefix=prefix, execute=execute,
        )
        applied = res.pop("applied", 0)
        grand_applied += applied
        scope = acct or "primary"
        if res:
            click.echo(f"[{scope}]")
            for name, n in sorted(res.items(), key=lambda kv: -kv[1]):
                click.echo(f"   {name}: {n}")
                grand_total += n

    if execute:
        click.echo(f"\napply-labels: applied labels to {grand_applied} message(s) "
                   f"across {len(targets)} mailbox(es).")
    else:
        click.echo(f"\nDRY RUN: would label {grand_total} message(s) "
                   f"(window={months}m). Re-run with --execute to apply.")


@cli.command(name="refresh-labels")
@click.option("--account", default=None, help="Scope to a single mailbox (default: all).")
def refresh_labels(account):
    """Resolve & store each email's real Gmail labels (DB-only; no Gmail writes)."""
    db = _get_db()
    accounts = MailMindConfig.load_accounts()
    targets = [account] if account else (accounts or [None])
    total = 0
    for acct in targets:
        auth_account = _auth_account_for(acct) if acct else None
        is_primary = (acct is None) or (accounts and acct == accounts[0])
        total += _refresh_labels_one_account(db, acct, auth_account, bool(is_primary))
    click.echo(f"refresh-labels: {total} emails now carry user labels.")


def _refresh_labels_one_account(db: Database, account, auth_account, allow_interactive) -> int:
    """Resolve each email's Gmail label IDs to truth-label names; store on emails.user_labels."""
    from mailmind.intelligence.labels import truth_label_policy, resolve_truth_labels
    creds = load_stored_credentials(auth_account)
    if creds is None:
        if allow_interactive:
            creds = authenticate(account=auth_account)
        else:
            LOG.warning("refresh-labels: no creds for %s — skipping.", account or "primary")
            return 0
    fetcher = GmailFetcher(build_gmail_service(creds))
    id_to_name = fetcher.list_label_map()
    if id_to_name:
        db.upsert_label_map(account, id_to_name)
    include, exclude = truth_label_policy()
    rows = db.execute_sql(
        "SELECT gmail_id, labels FROM emails WHERE account IS ?", (account,)
    ).fetchall()
    updated = 0
    for r in rows:
        ids = [x for x in (r["labels"] or "").split(",") if x]
        names = resolve_truth_labels(ids, id_to_name, include, exclude)
        db.set_email_user_labels(r["gmail_id"], ",".join(names))
        if names:
            updated += 1
    LOG.info("refresh-labels[%s]: %d/%d emails got user labels.",
             account or "primary", updated, len(rows))
    return updated


def _auth_account_for(account: Optional[str]) -> Optional[str]:
    """Map a mailbox to its token-storage identity.

    The primary mailbox (first in MAILMIND_ACCOUNTS, or an unconfigured
    single account) uses the legacy token storage (None) so existing
    deployments keep working and the watch loop's primary path finds it.
    Secondary mailboxes store under their own per-account token.
    """
    accounts = MailMindConfig.load_accounts()
    primary = accounts[0] if accounts else None
    if account is None or account == primary:
        return None
    return account


@cli.command()
@click.option("--account", default=None,
              help="Mailbox email to connect (default: the primary account).")
def auth(account: Optional[str]) -> None:
    """Run the interactive OAuth2 flow for a mailbox and persist the token.

    Connect the primary mailbox with no argument; connect a second mailbox
    with --account you@example.com (must be listed in MAILMIND_ACCOUNTS).
    """
    auth_account = _auth_account_for(account)
    label = account or (MailMindConfig.load_accounts() or ["primary"])[0]
    LOG.info("Starting OAuth2 flow for %s…", label)
    creds = authenticate(account=auth_account)
    LOG.info("Authentication successful for %s. Token stored securely.", label)
    LOG.info("Scopes granted: %s", creds.scopes)


@cli.command()
def accounts() -> None:
    """List configured mailboxes and whether each has a stored token."""
    from mailmind.ingestion.auth import _load_stored_token

    configured = MailMindConfig.load_accounts()
    if not configured:
        click.echo("No accounts configured (set MAILMIND_ACCOUNTS or MAILMIND_USER_EMAIL).")
        return
    click.echo("Configured mailboxes:")
    for i, acct in enumerate(configured):
        auth_account = None if i == 0 else acct
        connected = _load_stored_token(auth_account) is not None
        tag = "primary" if i == 0 else "secondary"
        status = "connected" if connected else "NOT connected"
        click.echo(f"  - {acct}  [{tag}]  {status}")


if __name__ == "__main__":
    cli()
