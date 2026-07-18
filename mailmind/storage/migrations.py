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
    (
        "0017_add_performance_indexes",
        """
        CREATE INDEX IF NOT EXISTS idx_aq_status_account_priority
            ON action_queue(status, account, priority_score, created_at);
        CREATE INDEX IF NOT EXISTS idx_aq_email_gmail_id
            ON action_queue(email_gmail_id);
        CREATE INDEX IF NOT EXISTS idx_predictions_account_created
            ON predictions(account, created_at);
        CREATE INDEX IF NOT EXISTS idx_predictions_label
            ON predictions(primary_label);
        CREATE INDEX IF NOT EXISTS idx_predictions_channel
            ON predictions(channel);
        CREATE INDEX IF NOT EXISTS idx_sender_profiles_trust_tier
            ON sender_profiles(trust_tier);
        """,
    ),
    (
        "0018_add_user_labels_and_label_map",
        """-- Handled in apply_migrations: emails.user_labels column + gmail_label_map table.""",
    ),
    (
        "0019_add_sender_and_thread_label_rules",
        """-- Handled in apply_migrations: sender_label_rules + thread_label_rules tables.""",
    ),
    (
        "0020_create_label_priority",
        """
        CREATE TABLE IF NOT EXISTS label_priority (
            label TEXT PRIMARY KEY,
            weight INTEGER CHECK (weight >= -20 AND weight <= 30)
        );
        """,
    ),
    (
        "0021_add_unsubscribe_url_to_emails",
        """-- Handled in apply_migrations: adds unsubscribe_url column to emails table.""",
    ),
    (
        "0022_add_match_pattern_to_sender_rules",
        """-- Handled in apply_migrations: adds match_pattern column to sender_label_rules.""",
    ),
    (
        "0023_add_predictions_created_at_index",
        """
        -- Training (ORDER BY p.created_at DESC) and analytics scan predictions by
        -- recency; index it now the table has grown to thousands of rows.
        CREATE INDEX IF NOT EXISTS idx_predictions_created_at ON predictions(created_at);
        """,
    ),
    (
        "0024_create_llm_usage",
        """
        -- Per-call LLM token usage + approximate cost + latency, so spend is
        -- visible over time (Track B) rather than only in transient logs.
        CREATE TABLE IF NOT EXISTS llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER,
            model TEXT,
            kind TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            latency_ms INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage(ts);
        """,
    ),
    (
        "0025_add_tier_source_to_sender_profiles",
        """-- Handled in apply_migrations: adds tier_source column to sender_profiles.""",
    ),
    (
        "0026_create_label_suggestions",
        """
        -- Periodic label-discovery output. The watch loop clusters the recent
        -- email content window and proposes NEW labels the user doesn't have yet.
        -- Review-only: rows start 'pending' and the user accepts/dismisses in the
        -- dashboard. A UNIQUE(suggested_label) keeps re-runs idempotent — the same
        -- theme is not proposed twice.
        CREATE TABLE IF NOT EXISTS label_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggested_label TEXT NOT NULL,
            rationale TEXT,
            cluster_terms TEXT,
            example_gmail_ids TEXT,
            email_count INTEGER DEFAULT 0,
            score REAL,
            account TEXT,
            status TEXT DEFAULT 'pending',
            created_at INTEGER DEFAULT (strftime('%s','now')),
            reviewed_at INTEGER
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_label_suggestions_label
            ON label_suggestions(suggested_label);
        CREATE INDEX IF NOT EXISTS idx_label_suggestions_status
            ON label_suggestions(status);
        """,
    ),
    (
        "0027_extend_emails_for_content_and_threading",
        """-- Handled in apply_migrations: adds body_html/message_id/in_reply_to/
        -- references_header/history_id columns to emails, plus a message_id index.""",
    ),
    (
        "0028_create_attachments",
        """
        -- Attachment METADATA only (never blobs) — filename/mime/size/gmail
        -- attachment id, so bytes can be fetched on demand via
        -- GmailFetcher.get_attachment() without bloating the DB (and its
        -- continuous Litestream replica) with binary content.
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_gmail_id TEXT NOT NULL,
            account TEXT,
            gmail_attachment_id TEXT NOT NULL,
            filename TEXT,
            mime_type TEXT,
            size_bytes INTEGER,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_attachments_email_attachment
            ON attachments(email_gmail_id, gmail_attachment_id);
        """,
    ),
    (
        "0029_add_account_to_sender_and_label_tables",
        """-- Handled in apply_migrations: adds nullable account TEXT to
        -- sender_profiles/sender_reputation/label_priority/user_corrections,
        -- backfilled via _primary_account() like migration 0015.""",
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

UNSUBSCRIBE_URL_COLUMN: List[Tuple[str, str]] = [("unsubscribe_url", "TEXT")]

# Optional subject-regex qualifier for a sender label rule. NULL = catch-all rule
# (applies to every message from the sender); non-NULL = conditional rule that only
# fires when the regex matches the subject — lets one listserv sender map to several
# labels by content. See queries.resolve_sender_label.
SENDER_RULE_PATTERN_COLUMN: List[Tuple[str, str]] = [("match_pattern", "TEXT")]

# Marks how a sender's trust_tier was set: 'auto' (recomputed from approval/
# rejection stats) or 'manual' (forced via Know/Mute). A manual tier must not be
# silently overwritten by the next auto-recompute. Existing rows default to 'auto'.
SENDER_TIER_SOURCE_COLUMN: List[Tuple[str, str]] = [("tier_source", "TEXT DEFAULT 'auto'")]

# Content/threading fields (Phase 1): full HTML body, RFC 5322 threading
# headers, and the mailbox history cursor. Promoted from dynamic setattr on
# the Email dataclass (parser.py) to real persisted columns.
EMAIL_CONTENT_THREADING_COLUMNS: List[Tuple[str, str]] = [
    ("body_html", "TEXT"),
    ("message_id", "TEXT"),
    ("in_reply_to", "TEXT"),
    ("references_header", "TEXT"),
    ("history_id", "INTEGER"),
]

# Nullable account column added to per-sender/label tables (no PK rebuild —
# see Phase 1 plan's guiding decisions on cross-account collision risk).
ACCOUNT_SCOPED_TABLES = (
    "sender_profiles",
    "sender_reputation",
    "label_priority",
    "user_corrections",
)


def _apply_sender_and_label_account_dimension(conn: sqlite3.Connection) -> None:
    """Add nullable `account` to sender/label tables and backfill existing rows.

    Mirrors `_apply_account_dimension()` (migration 0015) exactly: same
    `_primary_account()` resolution, same backfill-then-index shape. Column
    only — no primary key or uniqueness constraint change (whether trust
    should be shared or split across mailboxes is left open deliberately).
    """
    primary = _primary_account()
    for table in ACCOUNT_SCOPED_TABLES:
        _ensure_columns(conn, table, [("account", "TEXT")])
        conn.execute(
            f"UPDATE {table} SET account = ? WHERE account IS NULL", (primary,)
        )

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
        elif name == "0018_add_user_labels_and_label_map":
            _ensure_columns(conn, "emails", [("user_labels", "TEXT")])
            conn.execute(
                "CREATE TABLE IF NOT EXISTS gmail_label_map ("
                " account TEXT, label_id TEXT, name TEXT,"
                " PRIMARY KEY (account, label_id))"
            )
        elif name == "0019_add_sender_and_thread_label_rules":
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sender_label_rules ("
                " sender_email TEXT NOT NULL,"
                " label TEXT NOT NULL,"
                " account TEXT,"
                " created_at INTEGER DEFAULT (strftime('%s','now')),"
                " PRIMARY KEY (sender_email, label, account))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS thread_label_rules ("
                " thread_id TEXT PRIMARY KEY,"
                " label TEXT NOT NULL,"
                " created_at INTEGER DEFAULT (strftime('%s','now')))"
            )
        elif name == "0021_add_unsubscribe_url_to_emails":
            _ensure_columns(conn, "emails", UNSUBSCRIBE_URL_COLUMN)
        elif name == "0022_add_match_pattern_to_sender_rules":
            _ensure_columns(conn, "sender_label_rules", SENDER_RULE_PATTERN_COLUMN)
        elif name == "0025_add_tier_source_to_sender_profiles":
            _ensure_columns(conn, "sender_profiles", SENDER_TIER_SOURCE_COLUMN)
        elif name == "0027_extend_emails_for_content_and_threading":
            _ensure_columns(conn, "emails", EMAIL_CONTENT_THREADING_COLUMNS)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emails_message_id ON emails(message_id)"
            )
        elif name == "0029_add_account_to_sender_and_label_tables":
            _apply_sender_and_label_account_dimension(conn)
        else:
            cur.executescript(sql)
        cur.execute(
            "INSERT INTO _migrations (name, applied_at) VALUES (?, ?)",
            (name, int(datetime.now(UTC).timestamp())),
        )
        conn.commit()
