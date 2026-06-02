"""Migration definitions and application helpers for MailMind SQLite schema.

This module defines a simple linear set of migrations and an apply_migrations(conn)
helper that records applied migrations in a dedicated table.
"""
from __future__ import annotations

import os
from typing import List, Tuple
import sqlite3
from datetime import datetime, UTC


MIGRATIONS: List[Tuple[str, str]] = [
    (
        "0001_create_core_tables",
        """
        -- emails table: minimal normalized email record
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_id TEXT NOT NULL UNIQUE,
            thread_id TEXT,
            sender TEXT,
            recipients TEXT,
            subject TEXT,
            snippet TEXT,
            body_text TEXT,
            date_ts INTEGER,
            labels TEXT,
            parsed INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );

        CREATE INDEX IF NOT EXISTS idx_emails_sender ON emails(sender);
        CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date_ts);
        """,
    ),
    (
        "0002_create_predictions",
        """
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_gmail_id TEXT NOT NULL,
            model TEXT NOT NULL,
            labels TEXT,
            score INTEGER,
            priority_score INTEGER,
            confidence REAL,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_predictions_email ON predictions(email_gmail_id);
        CREATE INDEX IF NOT EXISTS idx_predictions_priority ON predictions(priority_score);
        """,
    ),
    (
        "0003_create_actions_applied",
        """
        CREATE TABLE IF NOT EXISTS actions_applied (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_gmail_id TEXT NOT NULL,
            action TEXT NOT NULL,
            params TEXT,
            dry_run INTEGER DEFAULT 1,
            succeeded INTEGER,
            details TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_actions_email ON actions_applied(email_gmail_id);
        """,
    ),
    (
        "0004_create_feedback",
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_gmail_id TEXT NOT NULL,
            user_feedback TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_email ON feedback(email_gmail_id);
        """,
    ),
    (
        "0005_create_sender_reputation",
        """
        CREATE TABLE IF NOT EXISTS sender_reputation (
            sender TEXT PRIMARY KEY,
            score REAL,
            last_seen INTEGER
        );
        """,
    ),
    (
        "0006_create_system_state",
        """
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at INTEGER
        );
        """,
    ),
    (
        "0007_extend_predictions_for_pipeline",
        """
        -- Handled in apply_migrations() with per-column existence checks.
        -- Adds the core pipeline fields needed by the dashboard and tests.
        """,
    ),
    (
        "0008_create_action_queue",
        """
        CREATE TABLE IF NOT EXISTS action_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_gmail_id TEXT NOT NULL,
            prediction_id INTEGER,
            action TEXT NOT NULL,
            params_json TEXT,
            action_fingerprint TEXT,
            status TEXT DEFAULT 'pending',
            confidence REAL,
            priority_score INTEGER,
            reason_json TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now')),
            updated_at INTEGER DEFAULT (strftime('%s','now')),
            reviewed_at INTEGER,
            executed_at INTEGER,
            FOREIGN KEY (prediction_id) REFERENCES predictions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_action_queue_status ON action_queue(status);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_action_queue_fingerprint ON action_queue(action_fingerprint);
        """,
    ),
    (
        "0008a_add_prediction_id_to_action_queue",
        """
        -- No-op compatibility migration.
        -- `prediction_id` is already created by 0008_create_action_queue.
        """,
    ),
    (
        "0009_create_user_corrections",
        """
        CREATE TABLE IF NOT EXISTS user_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_gmail_id TEXT NOT NULL,
            original_label TEXT,
            corrected_label TEXT,
            original_action TEXT,
            corrected_action TEXT,
            source TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_user_corrections_email ON user_corrections(email_gmail_id);
        """,
    ),
    (
        "0010_extend_predictions_for_llm_metadata",
        """
        -- Handled in apply_migrations() with per-column existence checks.
        -- Adds the LLM metadata used by the dashboard and pipeline audit trail.
        """,
    ),
    (
        "0011_extend_action_queue",
        """-- No-op placeholder; handled in Python with column existence checks."""
    ),
    (
        "0012_add_thread_context_to_predictions",
        """-- No-op placeholder; handled in Python with column existence checks."""
    ),
    (
        "0013_create_sender_profiles",
        """
        CREATE TABLE IF NOT EXISTS sender_profiles (
            sender_email TEXT PRIMARY KEY,
            display_name TEXT,
            total_seen INTEGER DEFAULT 0,
            total_approved INTEGER DEFAULT 0,
            total_rejected INTEGER DEFAULT 0,
            last_action_ts INTEGER,
            trust_tier TEXT DEFAULT 'neutral',
            auto_action_eligible INTEGER DEFAULT 0
        );
        """,
    ),
    (
        "0014_dedupe_predictions_one_per_email",
        """
        -- Collapse the predictions table to one row per email (latest wins).
        -- The watch loop historically re-classified the same emails every cycle,
        -- inserting a new row each time (~33 rows/email). Keep MAX(id) per email,
        -- repoint any action_queue references to the surviving row, then enforce
        -- uniqueness so save_prediction can upsert going forward.
        DELETE FROM predictions
        WHERE id NOT IN (
            SELECT MAX(id) FROM predictions GROUP BY email_gmail_id
        );

        UPDATE action_queue
        SET prediction_id = (
            SELECT MAX(p.id) FROM predictions p
            WHERE p.email_gmail_id = action_queue.email_gmail_id
        )
        WHERE EXISTS (
            SELECT 1 FROM predictions p
            WHERE p.email_gmail_id = action_queue.email_gmail_id
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_email_unique
            ON predictions(email_gmail_id);
        """,
    ),
    (
        "0015_add_account_dimension",
        """
        -- Handled in apply_migrations() with per-column checks + backfill.
        -- Adds an `account` column to the per-email tables so the dashboard
        -- and watch loop can separate two mailboxes. Existing rows are
        -- backfilled to the primary account (MAILMIND_ACCOUNTS[0] /
        -- MAILMIND_USER_EMAIL). Gmail message ids are effectively globally
        -- unique, so existing UNIQUE constraints are left as-is; `account`
        -- is a filtering/attribution column.
        """,
    ),
    (
        "0016_add_channel_to_predictions",
        """-- No-op placeholder; handled in Python with column existence checks.""",
    ),
]

PREDICTION_PIPELINE_COLUMNS: List[Tuple[str, str]] = [
    ("primary_label", "TEXT"),
    ("pipeline_used", "TEXT DEFAULT 'rules'"),
    ("action_suggested", "TEXT"),
    ("rule_matches", "TEXT"),
    ("scoring_breakdown", "TEXT"),
    ("ml_confidence", "REAL"),
    ("llm_confidence", "REAL"),
]

PREDICTION_LLM_COLUMNS: List[Tuple[str, str]] = [
    ("llm_label", "TEXT"),
    ("llm_rationale", "TEXT"),
    ("llm_action_hint", "TEXT"),
    ("llm_needs_review", "INTEGER DEFAULT 0"),
    ("classifier_source", "TEXT DEFAULT 'rules'"),
    ("llm_called_at", "TEXT"),
]

ACTION_QUEUE_COLUMNS: List[Tuple[str, str]] = [
    ("action", "TEXT"),
    ("params_json", "TEXT"),
    ("action_fingerprint", "TEXT"),
    ("priority_score", "INTEGER"),
    ("reason_json", "TEXT"),
    ("updated_at", "INTEGER"),
    ("executed_at", "INTEGER"),
]

THREAD_CONTEXT_COLUMN: List[Tuple[str, str]] = [("thread_context_json", "TEXT")]

CHANNEL_COLUMN: List[Tuple[str, str]] = [("channel", "TEXT")]

def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _migrations (
            name TEXT PRIMARY KEY,
            applied_at INTEGER
        );
        """
    )


def _get_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cur.fetchall()}


def _ensure_columns(
    conn: sqlite3.Connection,
    table_name: str,
    columns: List[Tuple[str, str]],
) -> None:
    existing = _get_table_columns(conn, table_name)
    for column_name, column_ddl in columns:
        if column_name in existing:
            continue
        conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_ddl}"
        )
        existing.add(column_name)


ACCOUNT_TABLES = ("emails", "predictions", "action_queue")


def _primary_account() -> str:
    """Resolve the primary account email for backfilling existing rows."""
    raw = os.environ.get("MAILMIND_ACCOUNTS", "").strip() or os.environ.get(
        "MAILMIND_USER_EMAIL", ""
    ).strip()
    accounts = [a.strip() for a in raw.split(",") if a.strip()]
    return accounts[0] if accounts else "primary"


def _apply_account_dimension(conn: sqlite3.Connection) -> None:
    """Add the `account` column to per-email tables and backfill existing rows.

    Gmail message ids are effectively globally unique, so existing UNIQUE
    constraints are kept; `account` is a filtering/attribution column.
    """
    primary = _primary_account()
    for table in ACCOUNT_TABLES:
        _ensure_columns(conn, table, [("account", "TEXT")])
        conn.execute(
            f"UPDATE {table} SET account = ? WHERE account IS NULL", (primary,)
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_account ON {table}(account)"
        )


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply any unapplied migrations in order.

    This function is idempotent and records applied migrations in the
    _migrations table.
    """
    _ensure_migrations_table(conn)
    cur = conn.cursor()
    for name, sql in MIGRATIONS:
        cur.execute("SELECT 1 FROM _migrations WHERE name = ?", (name,))
        if cur.fetchone():
            continue
        if name == "0007_extend_predictions_for_pipeline":
            _ensure_columns(conn, "predictions", PREDICTION_PIPELINE_COLUMNS)
        elif name == "0010_extend_predictions_for_llm_metadata":
            _ensure_columns(conn, "predictions", PREDICTION_LLM_COLUMNS)
        elif name == "0011_extend_action_queue":
            _ensure_columns(conn, "action_queue", ACTION_QUEUE_COLUMNS)
        elif name == "0012_add_thread_context_to_predictions":
            _ensure_columns(conn, "predictions", THREAD_CONTEXT_COLUMN)
        elif name == "0015_add_account_dimension":
            _apply_account_dimension(conn)
        elif name == "0016_add_channel_to_predictions":
            _ensure_columns(conn, "predictions", CHANNEL_COLUMN)
        else:
            cur.executescript(sql)
        cur.execute(
            "INSERT INTO _migrations (name, applied_at) VALUES (?, ?)",
            (name, int(datetime.now(UTC).timestamp())),
        )
        conn.commit()
