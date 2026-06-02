"""Database abstraction for MailMind using SQLite.

Provides a small, privacy-conscious API to initialize and access the core
SQLite schema. This keeps SQL behind a single module so higher layers can stay
clean and testable.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional, List
import json
from contextlib import contextmanager

from . import migrations
from .models import Email, Prediction, ActionApplied, SenderReputation


class Database:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser()
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self.connect()
        assert self._conn is not None
        migrations.apply_migrations(self._conn)

    def connect(self) -> None:
        if self._conn:
            return
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        # Return rows as dict-like access
        self._conn.row_factory = sqlite3.Row
        # Use WAL for better concurrency
        self._conn.execute("PRAGMA journal_mode=WAL;")

    @contextmanager
    def transaction(self):
        assert self._conn is not None
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        if self._conn:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    # --- Email operations ---
    def insert_email(self, email: Email) -> int:
        """Insert or ignore an email by gmail_id. Returns the integer row id.

        Does not log or print full bodies. Caller must respect privacy settings.
        """
        assert self._conn is not None
        sql = (
            "INSERT OR IGNORE INTO emails"
            " (gmail_id, thread_id, sender, recipients, subject, snippet, body_text, date_ts, labels, parsed, account)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);"
        )
        with self.transaction() as cur:
            cur.execute(sql, email.to_db_tuple())
            # If row was ignored, try to fetch existing id
            if cur.lastrowid:
                return int(cur.lastrowid)
            cur.execute("SELECT id FROM emails WHERE gmail_id = ?", (email.gmail_id,))
            row = cur.fetchone()
            return int(row["id"]) if row else -1

    def get_email_by_gmail_id(self, gmail_id: str) -> Optional[sqlite3.Row]:
        assert self._conn is not None
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM emails WHERE gmail_id = ?", (gmail_id,))
        return cur.fetchone()

    def list_emails(self, limit: int = 50, offset: int = 0) -> List[sqlite3.Row]:
        assert self._conn is not None
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM emails ORDER BY date_ts DESC LIMIT ? OFFSET ?", (limit, offset))
        return cur.fetchall()

    # --- Predictions ---
    def save_prediction(self, pred: Prediction) -> int:
        """Save a Prediction to the database, including all extended fields for Pass 3+.

        Handles backward compatibility with existing rows that may lack new columns.
        """
        assert self._conn is not None
        labels_s = ",".join(pred.labels) if pred.labels else None
        rule_matches_s = ",".join(pred.rule_matches) if pred.rule_matches else None

        # Upsert: one prediction row per email (latest classification wins).
        # A re-classification of the same email updates the existing row in place
        # rather than appending, so the table stays at one row per email.
        # Requires the UNIQUE index on email_gmail_id (migration 0014).
        sql = (
            "INSERT INTO predictions"
            " (email_gmail_id, model, labels, score, priority_score, confidence,"
            "  primary_label, pipeline_used, action_suggested, rule_matches, scoring_breakdown, thread_context_json,"
            "  ml_confidence, llm_confidence, llm_label, llm_rationale, llm_action_hint,"
            "  llm_needs_review, classifier_source, llm_called_at, created_at, account, channel)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(email_gmail_id) DO UPDATE SET"
            "  model=excluded.model, labels=excluded.labels, score=excluded.score,"
            "  priority_score=excluded.priority_score, confidence=excluded.confidence,"
            "  primary_label=excluded.primary_label, pipeline_used=excluded.pipeline_used,"
            "  action_suggested=excluded.action_suggested, rule_matches=excluded.rule_matches,"
            "  scoring_breakdown=excluded.scoring_breakdown, thread_context_json=excluded.thread_context_json,"
            "  ml_confidence=excluded.ml_confidence, llm_confidence=excluded.llm_confidence,"
            "  llm_label=excluded.llm_label, llm_rationale=excluded.llm_rationale,"
            "  llm_action_hint=excluded.llm_action_hint, llm_needs_review=excluded.llm_needs_review,"
            "  classifier_source=excluded.classifier_source, llm_called_at=excluded.llm_called_at,"
            "  created_at=excluded.created_at, account=excluded.account, channel=excluded.channel;"
        )
        with self.transaction() as cur:
            cur.execute(
                sql,
                (
                    pred.email_gmail_id,
                    pred.model,
                    labels_s,
                    pred.score,  # Keep for backward compat
                    pred.priority_score,
                    pred.confidence,
                    pred.primary_label,
                    pred.pipeline_used,
                    pred.action_suggested,
                    rule_matches_s,
                    pred.scoring_breakdown,
                    getattr(pred, 'thread_context_json', None),
                    pred.ml_confidence,
                    pred.llm_confidence,
                    pred.llm_label,
                    pred.llm_rationale,
                    pred.llm_action_hint,
                    int(bool(pred.llm_needs_review)),
                    pred.classifier_source,
                    pred.llm_called_at,
                    pred.created_at or int(__import__("time").time()),
                    pred.account,
                    getattr(pred, 'channel', None),
                ),
            )
            # lastrowid is unreliable on the ON CONFLICT UPDATE path; resolve
            # the row id explicitly so callers (and the queue FK) get the right one.
            cur.execute(
                "SELECT id FROM predictions WHERE email_gmail_id = ?",
                (pred.email_gmail_id,),
            )
            row = cur.fetchone()
            return int(row["id"]) if row else int(cur.lastrowid)

    def get_predictions_for_email(self, gmail_id: str) -> List[sqlite3.Row]:
        assert self._conn is not None
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM predictions WHERE email_gmail_id = ? ORDER BY created_at DESC", (gmail_id,))
        return cur.fetchall()

    def has_prediction(self, gmail_id: str) -> bool:
        """Return True if this email has already been classified.

        Cheap existence check used by the watch loop to skip re-processing
        (and re-calling the LLM on) emails it has already seen.
        """
        assert self._conn is not None
        cur = self._conn.cursor()
        cur.execute(
            "SELECT 1 FROM predictions WHERE email_gmail_id = ? LIMIT 1", (gmail_id,)
        )
        return cur.fetchone() is not None

    # --- Action Queue ---
    def update_action_queue_status(self, queue_id: int, status: str) -> None:
        """Update the status of an item in the action_queue.

        Args:
            queue_id: The id of the row in action_queue.
            status: One of 'pending', 'approved', 'rejected', 'executed'.

        Raises:
            ValueError: If status is not one of the allowed values.
        """
        allowed_statuses = {'pending', 'approved', 'rejected', 'executed'}
        if status not in allowed_statuses:
            raise ValueError(
                f"Invalid status '{status}'. Must be one of {allowed_statuses}"
            )

        sql = "UPDATE action_queue SET status = ? WHERE id = ?"
        with self.transaction() as cur:
            cur.execute(sql, (status, queue_id))

    # --- Actions logging ---
    def log_action(self, action: ActionApplied) -> int:
        assert self._conn is not None
        params_s = json.dumps(action.params or {})
        sql = (
            "INSERT INTO actions_applied (email_gmail_id, action, params, dry_run, succeeded, details, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?);"
        )
        with self.transaction() as cur:
            cur.execute(sql, (action.email_gmail_id, action.action, params_s, int(bool(action.dry_run)),
                              None if action.succeeded is None else int(bool(action.succeeded)),
                              action.details, action.created_at))
            return int(cur.lastrowid)

    # --- Sender reputation ---
    def get_sender_reputation(self, sender: str) -> Optional[SenderReputation]:
        assert self._conn is not None
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM sender_reputation WHERE sender = ?", (sender,))
        row = cur.fetchone()
        if not row:
            return None
        return SenderReputation(sender=row["sender"], score=row["score"], last_seen=row["last_seen"])

    def get_sender_trust_score(self, sender: str, default: float = 0.0) -> float:
        """Get sender trust score safely, returning a default if not found or invalid.
        
        Args:
            sender: Sender email address.
            default: Default trust score if sender not found (0.0-10.0).
        
        Returns:
            Trust score clamped to 0.0-10.0 range.
        """
        try:
            rep = self.get_sender_reputation(sender)
            if rep and rep.score is not None:
                return max(0.0, min(10.0, float(rep.score)))
            return default
        except Exception:
            return default

    def update_sender_reputation(self, sender: str, score: float) -> None:
        assert self._conn is not None
        cur = self._conn.cursor()
        with self.transaction() as cur:
            cur.execute(
                "INSERT INTO sender_reputation (sender, score, last_seen) VALUES (?, ?, ?)"
                " ON CONFLICT(sender) DO UPDATE SET score = excluded.score, last_seen = excluded.last_seen",
                (sender, float(score), int(__import__("time").time())),
            )

    # --- System state (key/value) ---
    def get_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        assert self._conn is not None
        cur = self._conn.cursor()
        cur.execute("SELECT value FROM system_state WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        assert self._conn is not None
        with self.transaction() as cur:
            cur.execute(
                "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                (key, str(value), int(time.time())),
            )

    # --- Retention / maintenance ---
    def prune_old_data(self, retention_days: int = 90) -> dict:
        """Delete locally-cached data older than ``retention_days``.

        Removes old emails and their associated predictions / actions /
        queue items / corrections / feedback from the LOCAL SQLite cache.
        This never touches the Gmail account — the messages still exist in
        Gmail. Emails that still have a *pending* action-queue item are
        preserved regardless of age, so nothing awaiting review is dropped.
        Emails with a NULL date_ts are also preserved (we can't age them).

        Returns a dict of {table: rows_deleted}.
        """
        assert self._conn is not None
        cutoff = int(time.time()) - retention_days * 86400
        prunable = (
            "SELECT gmail_id FROM emails"
            " WHERE date_ts IS NOT NULL AND date_ts < ?"
            " AND gmail_id NOT IN (SELECT email_gmail_id FROM action_queue WHERE status = 'pending')"
        )
        counts: dict = {}
        with self.transaction() as cur:
            # Children first; emails are deleted last so the subquery stays valid.
            for table in (
                "predictions",
                "actions_applied",
                "feedback",
                "action_queue",
                "user_corrections",
            ):
                cur.execute(
                    f"DELETE FROM {table} WHERE email_gmail_id IN ({prunable})",
                    (cutoff,),
                )
                counts[table] = cur.rowcount
            cur.execute(f"DELETE FROM emails WHERE gmail_id IN ({prunable})", (cutoff,))
            counts["emails"] = cur.rowcount
        return counts

    def vacuum(self) -> None:
        """Checkpoint the WAL and reclaim disk space. Run outside a transaction."""
        assert self._conn is not None
        self._conn.commit()
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        self._conn.execute("VACUUM;")

    # --- Utilities for tests / migrations ---
    def execute_sql(self, sql: str, params: Optional[tuple] = None) -> sqlite3.Cursor:
        assert self._conn is not None
        cur = self._conn.cursor()
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        return cur


def open_database_from_config_path(db_path: str | Path) -> Database:
    return Database(Path(db_path))
