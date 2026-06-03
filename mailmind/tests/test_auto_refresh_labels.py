from __future__ import annotations

import tempfile, pathlib, time
from unittest.mock import MagicMock
import mailmind.main as main_mod
from mailmind.main import _process_message_id, _maybe_refresh_labels
from mailmind.storage.database import Database
from mailmind.storage.models import Email


def _db():
    return Database(pathlib.Path(tempfile.mkdtemp()) / "t.db")


def test_process_message_id_sets_user_labels():
    db = _db()
    fetcher = MagicMock(); fetcher.get_message.return_value = {"id": "m1"}
    pipeline = MagicMock(); pipeline.db = db
    pipeline.db_has = db.has_prediction
    pipeline.process.return_value = MagicMock(primary_label="X", priority_score=10,
                                              scoring_breakdown=None, id=1)
    qm = MagicMock()
    orig = main_mod.parse_message
    main_mod.parse_message = MagicMock(return_value=Email(
        gmail_id="m1", sender="a@b.com", account="acc", labels=["L1", "L9"]))
    try:
        db.upsert_label_map("acc", {"L1": "OE", "L9": "INBOX"})
        _process_message_id("m1", fetcher, pipeline, qm, account="acc",
                            prefetched_raw={"id": "m1"},
                            label_map={"L1": "OE", "L9": "INBOX"},
                            truth_include=[], truth_exclude=["AI/"])
    finally:
        main_mod.parse_message = orig
    row = db.execute_sql("SELECT user_labels FROM emails WHERE gmail_id='m1'").fetchone()
    assert row["user_labels"] == "OE"          # INBOX filtered out


def test_maybe_refresh_labels_respects_interval(monkeypatch):
    db = _db()
    calls = {"n": 0}
    monkeypatch.setattr(main_mod, "_refresh_labels_one_account",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(main_mod.MailMindConfig, "load_accounts", staticmethod(lambda: ["a@b.com"]))
    _maybe_refresh_labels(db, interval_seconds=86400)      # first run → fires
    first = calls["n"]
    _maybe_refresh_labels(db, interval_seconds=86400)      # within interval → skip
    assert calls["n"] == first and first >= 1
