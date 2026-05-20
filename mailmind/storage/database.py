"""Database abstraction for MailMind using SQLite.

Provides a small, privacy-conscious API to initialize and access the core
SQLite schema. This keeps SQL behind a single module so higher layers can stay
clean and testable.
"""
from __future__ import annotations

import sqlite3
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
            " (gmail_id, thread_id, sender, recipients, subject, snippet, body_text, date_ts, labels, parsed)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);"
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

        sql = (
            "INSERT INTO predictions"
            " (email_gmail_id, model, labels, score, priority_score, confidence,"
            "  primary_label, pipeline_used, action_suggested, rule_matches, scoring_breakdown,"
            "  ml_confidence, llm_confidence, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);"
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
                    pred.ml_confidence,
                    pred.llm_confidence,
                    pred.created_at or int(__import__("time").time()),
                ),
            )
            return int(cur.lastrowid)

    def get_predictions_for_email(self, gmail_id: str) -> List[sqlite3.Row]:
        assert self._conn is not None
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM predictions WHERE email_gmail_id = ? ORDER BY created_at DESC", (gmail_id,))
        return cur.fetchall()

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

