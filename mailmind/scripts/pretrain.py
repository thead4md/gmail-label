#!/usr/bin/env python3
"""Pre-training script for MailMind.

Fetches historical emails from Gmail, parses and stores them in the local SQLite DB,
runs them through the processing pipeline to generate labels/scores, then trains
the ML model on the labeled data.

Usage:
    python -m mailmind.scripts.pretrain --days 90
    python -m mailmind.scripts.pretrain --days 30 --dry-run
    python -m mailmind.scripts.pretrain --days 90 --max-emails 500

Environment variables:
    MAILMIND_DB_PATH    Path to SQLite database (default: ~/.mailmind/mailmind.db)
    MAILMIND_DATA_DIR   Data directory for model/credentials/tokens (default: ~/.mailmind)
"""
from __future__ import annotations

import os
import sys
import logging
import time
import argparse
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

# Ensure project is on path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mailmind.ingestion.auth import authenticate
from mailmind.ingestion.fetcher import GmailFetcher
from mailmind.ingestion.parser import parse_message
from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction
from mailmind.processing.pipeline import Pipeline
from mailmind.processing.rules import RulesEngine
from mailmind.processing.scorer import PriorityScorer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
LOG = logging.getLogger("pretrain")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_env_paths() -> tuple[Path, Path]:
    """Resolve DB and data-directory paths from environment or defaults."""
    db_path = Path(
        os.environ.get("MAILMIND_DB_PATH", "~/.mailmind/mailmind.db")
    ).expanduser()
    data_dir = Path(
        os.environ.get("MAILMIND_DATA_DIR", "~/.mailmind")
    ).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return db_path, data_dir


def _row_to_email(row) -> Email:
    """Reconstruct an Email object from a database row (sqlite3.Row)."""
    recipients_str = row["recipients"] or ""
    recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]

    labels_str = row["labels"] or ""
    labels = [lbl.strip() for lbl in labels_str.split(",") if lbl.strip()]

    return Email(
        gmail_id=row["gmail_id"],
        thread_id=row["thread_id"],
        sender=row["sender"],
        recipients=recipients,
        subject=row["subject"],
        snippet=row["snippet"],
        body_text=row["body_text"],
        date_ts=row["date_ts"],
        labels=labels,
        parsed=bool(row["parsed"]),
    )


def _list_message_ids_with_query(
    fetcher: GmailFetcher,
    query: str,
    max_results: int = 2000,
) -> List[str]:
    """Call the Gmail API messages.list with an arbitrary search query.

    The existing GmailFetcher.list_message_ids() does not expose `q`, so we
    call the service directly here, re-using its retry-friendly patterns.
    """
    ids: List[str] = []
    page_token: str | None = None

    while len(ids) < max_results:
        remaining = max_results - len(ids)
        try:
            resp = (
                fetcher.service.users()
                .messages()
                .list(
                    userId=fetcher.user_id,
                    q=query,
                    pageToken=page_token,
                    maxResults=min(500, remaining),
                )
                .execute()
            )
        except HttpError as exc:
            LOG.error("Failed to list messages (query=%r): %s", query, exc)
            break

        msgs = resp.get("messages", [])
        ids.extend(m["id"] for m in msgs)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
        time.sleep(fetcher.rate_limit_seconds)

    return ids[:max_results]


def _build_pipeline(db: Database) -> Pipeline:
    """Create a rules-only Pipeline suitable for label generation."""
    return Pipeline(
        db=db,
        rules_engine=RulesEngine(),
        scorer=PriorityScorer(),
        executor=None,
        safety_policy=None,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-train MailMind: fetch, process, and train on historical emails."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="How many days back to fetch (default: 90).",
    )
    parser.add_argument(
        "--max-emails",
        type=int,
        default=2000,
        help="Maximum number of emails to fetch (default: 2000).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse only; skip DB writes and training.",
    )
    args = parser.parse_args()

    db_path, data_dir = _get_env_paths()

    # ==================================================================
    # STEP 1 — SETUP
    # ==================================================================
    print("=" * 60)
    print("MailMind Pre-Training")
    print("=" * 60)
    print(f"  Database : {db_path}")
    print(f"  Data dir : {data_dir}")
    print(f"  Days back: {args.days}")
    print(f"  Max fetch: {args.max_emails}")
    print(f"  Dry run  : {args.dry_run}")
    print()

    # 1a. Database
    db = Database(db_path)
    LOG.info("Database initialised at %s", db_path)

    # 1b. Authenticate
    print("Authenticating with Gmail...")
    creds = authenticate()
    LOG.info("Authentication successful")

    # 1c. Gmail service & fetcher
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    fetcher = GmailFetcher(service)

    # ==================================================================
    # STEP 2 — FETCH HISTORICAL EMAILS
    # ==================================================================
    since_date = datetime.now(timezone.utc) - timedelta(days=args.days)
    query = f"after:{since_date.strftime('%Y/%m/%d')}"
    print(f"Querying Gmail with: {query}")

    msg_ids = _list_message_ids_with_query(fetcher, query, max_results=args.max_emails)
    total_to_fetch = len(msg_ids)
    print(f"Found {total_to_fetch} message IDs.")

    fetched_count = 0
    skipped_count = 0
    error_count = 0
    newly_fetched_ids: List[str] = []

    for i, msg_id in enumerate(msg_ids):
        # Check if already in DB
        existing = db.get_email_by_gmail_id(msg_id)
        if existing is not None:
            skipped_count += 1
        else:
            try:
                resource = fetcher.get_message(msg_id)
                email = parse_message(resource)

                if not args.dry_run:
                    db.insert_email(email)
                    try:
                        db.insert_attachments(
                            email.gmail_id, None, getattr(email, "attachments", []) or []
                        )
                    except Exception as exc:
                        LOG.warning(
                            "Failed to insert attachments for %s: %s", email.gmail_id, exc
                        )

                newly_fetched_ids.append(msg_id)
                fetched_count += 1
            except HttpError as exc:
                LOG.error("Gmail API error for %s: %s", msg_id, exc)
                error_count += 1
            except Exception:
                LOG.exception("Unexpected error fetching %s", msg_id)
                error_count += 1

        # Progress every 50 (including both fetched and skipped)
        total_done = fetched_count + skipped_count + error_count
        if total_done % 50 == 0 or total_done == total_to_fetch:
            print(
                f"Fetched {fetched_count}/{total_to_fetch} "
                f"(skipped: {skipped_count}, errors: {error_count})"
            )

    print()
    print(f"Fetch complete: {fetched_count} new, {skipped_count} skipped, {error_count} errors")
    print()

    # ==================================================================
    # STEP 3 — PROCESS & LABEL
    # ==================================================================
    processed_count = 0
    process_errors = 0

    if args.dry_run:
        print("DRY RUN — skipping pipeline processing and DB writes.")
    elif not newly_fetched_ids:
        print("No new emails to process.")
    else:
        pipeline = _build_pipeline(db)
        print(f"Running pipeline on {len(newly_fetched_ids)} newly fetched emails...")

        for i, gmail_id in enumerate(newly_fetched_ids):
            try:
                row = db.get_email_by_gmail_id(gmail_id)
                if row is None:
                    LOG.warning("Email %s not found in DB after insert; skipping.", gmail_id)
                    continue

                email = _row_to_email(row)
                pipeline.process(email, auto_action=False)
                processed_count += 1
            except Exception:
                LOG.exception("Error processing email %s", gmail_id)
                process_errors += 1

        print(f"Processed {processed_count} emails" + (
            f" ({process_errors} errors)" if process_errors else ""
        ))
    print()

    # ==================================================================
    # STEP 4 — TRAIN
    # ==================================================================
    model_path: Path | None = None
    trained = False

    if args.dry_run:
        print("DRY RUN — skipping model training.")
    else:
        print("Training ML model on labeled data...")

        # train_model_from_db saves to its default location; we copy
        # to MAILMIND_DATA_DIR/model.pkl afterwards.
        from mailmind.ml.train import train_model_from_db

        classifier = train_model_from_db(db)
        if classifier is not None:
            default_saved = classifier.get_model_path()
            model_path = data_dir / "model.pkl"
            shutil.copy(str(default_saved), str(model_path))
            trained = True

            # Print accuracy / metrics if available
            meta = classifier.metadata
            if meta:
                print(f"  Classes     : {meta.class_names}")
                print(f"  Samples     : {meta.num_samples}")
                print(f"  Version     : {meta.version}")
                if meta.accuracy is not None:
                    print(f"  Accuracy    : {meta.accuracy:.3f}")
                if hasattr(classifier, "_pipeline") and classifier._pipeline is not None:
                    try:
                        from sklearn.utils.validation import check_is_fitted

                        check_is_fitted(classifier._pipeline)
                        import numpy as np

                        # Quick self-evaluation on training data for approximate accuracy
                        corpus_rows = db.execute_sql(
                            "SELECT p.email_gmail_id, p.primary_label, e.subject, e.snippet, e.sender, e.body_text "
                            "FROM predictions p JOIN emails e ON e.gmail_id = p.email_gmail_id "
                            "WHERE p.primary_label IS NOT NULL AND p.primary_label != '' "
                            "ORDER BY p.created_at DESC"
                        ).fetchall()

                        if corpus_rows:
                            eval_corpus: List[str] = []
                            eval_labels: List[str] = []
                            seen: set[str] = set()
                            for r in corpus_rows:
                                gid = r["email_gmail_id"]
                                if gid in seen:
                                    continue
                                seen.add(gid)
                                lbl = r["primary_label"]
                                # Build eval text the SAME way training does
                                # (build_model_text appends the engineered feat_*
                                # tokens). A raw join omits them, so the model is
                                # scored on text it never saw → misleading Train-acc.
                                from mailmind.ml.features import build_model_text
                                txt = build_model_text(
                                    r["subject"] or "",
                                    r["sender"] or "",
                                    r["snippet"] or "",
                                    (r["body_text"] or "")[:500],
                                ).strip()
                                if txt and lbl:
                                    eval_corpus.append(txt)
                                    eval_labels.append(lbl)

                            if eval_corpus and len(set(eval_labels)) >= 2:
                                preds = classifier.predict(eval_corpus)
                                correct = sum(1 for (pl, _), tl in zip(preds, eval_labels) if pl == tl)
                                train_acc = correct / len(eval_corpus)
                                print(f"  Train-acc   : {train_acc:.3f}")
                    except Exception:
                        LOG.debug("Could not compute training accuracy", exc_info=True)

            print(f"\nModel saved to {model_path}")
        else:
            print("⚠  Could not train model — insufficient labeled data.")
            print("   Run the pipeline on more emails first.")

    print()

    # ==================================================================
    # STEP 5 — SUMMARY
    # ==================================================================
    print("=" * 60)
    print("PRE-TRAINING SUMMARY")
    print("=" * 60)
    print(f"✅ Fetched: {fetched_count} emails")
    print(f"✅ Processed: {processed_count} emails")
    print(f"✅ Skipped (already in DB): {skipped_count} emails")
    if error_count:
        print(f"⚠️  Fetch errors: {error_count} emails")
    if process_errors:
        print(f"⚠️  Processing errors: {process_errors} emails")
    if trained and model_path is not None:
        print(f"✅ Model trained and saved to: {model_path}")
    elif not args.dry_run:
        print("⚠️  Model not trained (insufficient data or error)")
    print("✅ Pre-training complete!")

    db.close()


if __name__ == "__main__":
    main()