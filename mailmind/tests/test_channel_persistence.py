"""Tests for P0: channel detection wired into pipeline + persisted."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mailmind.storage.database import Database
from mailmind.storage.models import Email, Prediction


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        database = Database(Path(d) / "test.db")
        yield database
        database.close()


def test_migration_adds_channel_column(db):
    cols = {row[1] for row in db.execute_sql("PRAGMA table_info(predictions)").fetchall()}
    assert "channel" in cols


def test_save_prediction_persists_channel(db):
    db.insert_email(Email(gmail_id="c1", sender="x@y.com", subject="Hi"))
    pred = Prediction(
        email_gmail_id="c1", model="rules", labels=["WORK"],
        priority_score=70, primary_label="WORK", channel="newsletter",
    )
    db.save_prediction(pred)
    row = db.execute_sql(
        "SELECT channel FROM predictions WHERE email_gmail_id = ?", ("c1",)
    ).fetchone()
    assert row["channel"] == "newsletter"


def test_save_prediction_channel_defaults_none(db):
    db.insert_email(Email(gmail_id="c2", sender="x@y.com", subject="Hi"))
    pred = Prediction(
        email_gmail_id="c2", model="rules", labels=["WORK"],
        priority_score=70, primary_label="WORK",
    )
    db.save_prediction(pred)
    row = db.execute_sql(
        "SELECT channel FROM predictions WHERE email_gmail_id = ?", ("c2",)
    ).fetchone()
    assert row["channel"] is None


def test_pipeline_detects_and_persists_channel(db):
    from mailmind.processing.pipeline import Pipeline
    from mailmind.processing.rules import RulesEngine
    from mailmind.processing.scorer import PriorityScorer

    pipeline = Pipeline(db, RulesEngine(), PriorityScorer(), executor=None)
    email = Email(
        gmail_id="c3",
        sender="hirlevel@ceges.hu",
        subject="Heti hírlevél",
        body_text="Kattintson ide a leiratkozáshoz.",
    )
    db.insert_email(email)
    pipeline.process(email)
    row = db.execute_sql(
        "SELECT channel FROM predictions WHERE email_gmail_id = ?", ("c3",)
    ).fetchone()
    assert row["channel"] == "newsletter"
