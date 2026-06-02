"""PR A: busy_timeout set + performance indexes created."""
from __future__ import annotations
import tempfile, pathlib, pytest
from mailmind.storage.database import Database


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        database = Database(pathlib.Path(d) / "t.db")
        yield database
        database.close()


def test_busy_timeout_is_set(db):
    # PRAGMA busy_timeout returns the current value in milliseconds
    val = db._conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert val == 30000


def test_perf_indexes_exist(db):
    names = {r[0] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    for idx in (
        "idx_aq_status_account_priority",
        "idx_aq_email_gmail_id",
        "idx_predictions_account_created",
        "idx_predictions_label",
        "idx_predictions_channel",
        "idx_sender_profiles_trust_tier",
    ):
        assert idx in names, f"missing index {idx}"


def test_migration_is_idempotent(db):
    # Re-applying must not raise (all IF NOT EXISTS)
    from mailmind.storage.migrations import apply_migrations
    apply_migrations(db._conn)
    apply_migrations(db._conn)
