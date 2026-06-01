"""Tests for the watch-loop skip gate in _process_message_id.

The watch loop re-lists the inbox every poll cycle. Re-classifying an
email it has already seen wastes a Gmail fetch and (worse) an LLM call.
_process_message_id skips any message that already has a prediction,
before calling get_message(). These tests pin that behavior.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import mailmind.main as main_mod
from mailmind.main import _process_message_id


def _make_components(*, has_prediction: bool):
    """Build mock fetcher/pipeline/queue_manager for gate tests.

    pipeline.db.has_prediction drives the skip decision. The pipeline path
    is stubbed (no scoring_breakdown) so the queue manager is not exercised.
    """
    fetcher = MagicMock()
    fetcher.get_message.return_value = {"id": "msg"}
    pipeline = MagicMock()
    pipeline.db.has_prediction.return_value = has_prediction
    pipeline.process.return_value = MagicMock(
        primary_label="WORK", priority_score=50, scoring_breakdown=None, id=1
    )
    queue_manager = MagicMock()
    return fetcher, pipeline, queue_manager


def test_skips_already_classified_email_without_fetching():
    """An email with an existing prediction is skipped before get_message()."""
    fetcher, pipeline, queue_manager = _make_components(has_prediction=True)

    _process_message_id("already_seen", fetcher, pipeline, queue_manager)

    fetcher.get_message.assert_not_called()
    pipeline.process.assert_not_called()


def test_processes_unseen_email():
    """An email with no prediction is fetched and processed."""
    fetcher, pipeline, queue_manager = _make_components(has_prediction=False)

    original_parse = main_mod.parse_message
    main_mod.parse_message = MagicMock(
        return_value=MagicMock(gmail_id="new_email", primary_label="WORK")
    )
    try:
        _process_message_id("new_email", fetcher, pipeline, queue_manager)
    finally:
        main_mod.parse_message = original_parse

    fetcher.get_message.assert_called_once_with("new_email")
    pipeline.process.assert_called_once()


def test_reclassify_flag_forces_processing():
    """reclassify=True bypasses the skip even when a prediction exists."""
    fetcher, pipeline, queue_manager = _make_components(has_prediction=True)

    original_parse = main_mod.parse_message
    main_mod.parse_message = MagicMock(
        return_value=MagicMock(gmail_id="force_me", primary_label="WORK")
    )
    try:
        _process_message_id("force_me", fetcher, pipeline, queue_manager, reclassify=True)
    finally:
        main_mod.parse_message = original_parse

    fetcher.get_message.assert_called_once_with("force_me")
    pipeline.process.assert_called_once()
