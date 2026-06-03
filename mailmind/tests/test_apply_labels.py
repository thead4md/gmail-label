"""Tests for the bulk apply-labels feature (fetcher label ops + query + helper)."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mailmind.ingestion.fetcher import GmailFetcher
from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction
from mailmind.storage.queries import get_labeled_predictions


# ---------------------------------------------------------------------------
# fetcher.ensure_label
# ---------------------------------------------------------------------------

def test_ensure_label_returns_existing_id():
    service = MagicMock()
    service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": [{"id": "L1", "name": "MailMind/Work"}]
    }
    f = GmailFetcher(service, rate_limit_seconds=0)
    assert f.ensure_label("MailMind/Work") == "L1"
    service.users.return_value.labels.return_value.create.assert_not_called()


def test_ensure_label_creates_when_absent():
    service = MagicMock()
    service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": []
    }
    service.users.return_value.labels.return_value.create.return_value.execute.return_value = {
        "id": "NEW"
    }
    f = GmailFetcher(service, rate_limit_seconds=0)
    assert f.ensure_label("MailMind/Newsletter") == "NEW"


# ---------------------------------------------------------------------------
# fetcher.batch_add_label
# ---------------------------------------------------------------------------

def test_batch_add_label_uses_batchmodify_in_1000_chunks():
    service = MagicMock()
    bm = service.users.return_value.messages.return_value.batchModify
    f = GmailFetcher(service, rate_limit_seconds=0)

    ids = [str(i) for i in range(2500)]   # 3 chunks: 1000/1000/500
    submitted = f.batch_add_label(ids, "L1")
    assert submitted == 2500
    assert bm.call_count == 3
    # First call body carries the label + a 1000-id chunk
    _, kwargs = bm.call_args_list[0]
    assert kwargs["body"]["addLabelIds"] == ["L1"]
    assert len(kwargs["body"]["ids"]) == 1000


def test_batch_add_label_empty_inputs():
    f = GmailFetcher(MagicMock(), rate_limit_seconds=0)
    assert f.batch_add_label([], "L1") == 0
    assert f.batch_add_label(["a"], "") == 0


# ---------------------------------------------------------------------------
# query: get_labeled_predictions
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        database = Database(Path(d) / "t.db")
        yield database
        database.close()


def _seed(db, gid, label, account="acc"):
    db.insert_email(Email(gmail_id=gid, sender="a@b.com", subject="S", account=account))
    p = Prediction(email_gmail_id=gid, model="rules", labels=[label],
                   priority_score=70, primary_label=label, account=account)
    db.save_prediction(p)


def test_get_labeled_predictions_returns_label_pairs(db):
    _seed(db, "m1", "WORK")
    _seed(db, "m2", "NEWSLETTER")
    since = int(time.time()) - 86400
    rows = get_labeled_predictions(db, since_ts=since, account="acc")
    labels = {r["email_gmail_id"]: r["primary_label"] for r in rows}
    assert labels == {"m1": "WORK", "m2": "NEWSLETTER"}


def test_get_labeled_predictions_account_scoped(db):
    _seed(db, "m1", "WORK", account="acc")
    _seed(db, "m2", "WORK", account="other")
    since = int(time.time()) - 86400
    rows = get_labeled_predictions(db, since_ts=since, account="acc")
    assert [r["email_gmail_id"] for r in rows] == ["m1"]


def test_get_labeled_predictions_window_excludes_old(db):
    _seed(db, "m1", "WORK")
    future = int(time.time()) + 99999
    assert get_labeled_predictions(db, since_ts=future, account="acc") == []


# ---------------------------------------------------------------------------
# helper: _apply_labels_one_account dry-run counts (no Gmail calls)
# ---------------------------------------------------------------------------

def test_apply_labels_dry_run_counts_no_auth(db):
    import mailmind.main as main_mod
    _seed(db, "m1", "WORK", account="acc")
    _seed(db, "m2", "WORK", account="acc")
    _seed(db, "m3", "MASS_EMAIL", account="acc")
    since = int(time.time()) - 86400

    res = main_mod._apply_labels_one_account(
        db, since_ts=since, account="acc", auth_account=None,
        allow_interactive=False, prefix="MailMind/", execute=False,
    )
    assert res["applied"] == 0
    assert res["MailMind/Work"] == 2
    assert res["MailMind/Mass Email"] == 1


def test_friendly_label():
    import mailmind.main as main_mod
    assert main_mod._friendly_label("MASS_EMAIL") == "Mass Email"
    assert main_mod._friendly_label("WORK") == "Work"
