from __future__ import annotations

import tempfile
import pathlib
import pytest

from mailmind.intelligence.labels import is_truth_label, resolve_truth_labels, truth_label_policy
from mailmind.storage.database import Database


def test_excludes_system_and_ai_mailmind():
    inc, exc = [], ["AI/", "MailMind/"]
    assert is_truth_label("OE/ToDo", inc, exc) is True
    assert is_truth_label("AI/Work", inc, exc) is False
    assert is_truth_label("MailMind/Newsletter", inc, exc) is False
    assert is_truth_label("INBOX", inc, exc) is False
    assert is_truth_label("CATEGORY_UPDATES", inc, exc) is False


def test_include_allowlist_restricts():
    inc, exc = ["OE/"], ["AI/"]
    assert is_truth_label("OE/kész", inc, exc) is True
    assert is_truth_label("twobird/low", inc, exc) is False


def test_resolve_maps_and_filters():
    id_to_name = {"L1": "OE", "L2": "AI/Work", "L9": "INBOX"}
    assert resolve_truth_labels(["L1", "L2", "L9"], id_to_name, [], ["AI/"]) == ["OE"]


def test_default_policy_excludes_ai_mailmind(monkeypatch):
    monkeypatch.delenv("MAILMIND_TRUTH_LABELS_INCLUDE", raising=False)
    monkeypatch.delenv("MAILMIND_TRUTH_LABELS_EXCLUDE", raising=False)
    inc, exc = truth_label_policy()
    assert "AI/" in exc and "MailMind/" in exc


def test_migration_and_db_helpers():
    with tempfile.TemporaryDirectory() as d:
        db = Database(pathlib.Path(d) / "t.db")
        cols = {r[1] for r in db.execute_sql("PRAGMA table_info(emails)").fetchall()}
        assert "user_labels" in cols
        db.upsert_label_map("acc", {"L1": "OE"})
        assert db.get_label_map("acc") == {"L1": "OE"}
        from mailmind.storage.models import Email
        db.insert_email(Email(gmail_id="g1", sender="a@b.com", account="acc"))
        db.set_email_user_labels("g1", "OE,OE/ToDo")
        row = db.execute_sql("SELECT user_labels FROM emails WHERE gmail_id='g1'").fetchone()
        assert row["user_labels"] == "OE,OE/ToDo"
