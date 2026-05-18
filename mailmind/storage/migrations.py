"""Migration definitions and application helpers for MailMind SQLite schema.

This module defines a simple linear set of migrations and an apply_migrations(conn)
helper that records applied migrations in a dedicated table.
"""
from __future__ import annotations

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
        ALTER TABLE predictions ADD COLUMN primary_label TEXT;
        ALTER TABLE predictions ADD COLUMN pipeline_used TEXT DEFAULT 'rules';
        ALTER TABLE predictions ADD COLUMN action_suggested TEXT;
        ALTER TABLE predictions ADD COLUMN rule_matches TEXT;
        ALTER TABLE predictions ADD COLUMN scoring_breakdown TEXT;
        ALTER TABLE predictions ADD COLUMN ml_confidence REAL;
        ALTER TABLE predictions ADD COLUMN llm_confidence REAL;
        """,
    ),
]


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _migrations (
            name TEXT PRIMARY KEY,
            applied_at INTEGER
        );
        """
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
        # Execute migration SQL inside a transaction
        # For ALTER TABLE migrations, we catch and ignore "column already exists" errors
        try:
            cur.executescript(sql)
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower() or "column already exists" in str(e).lower():
                # Column already exists; skip (idempotent)
                pass
            else:
                raise
        cur.execute(
            "INSERT INTO _migrations (name, applied_at) VALUES (?, ?)",
            (name, int(datetime.now(UTC).timestamp())),
        )
        conn.commit()

