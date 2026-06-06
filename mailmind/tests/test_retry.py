"""Regression tests for fetcher._retry transient-error handling (I7 fix).

Before: _retry only caught HttpError, so a dropped connection / SSL blip / timeout
during a fetch propagated on the first attempt and aborted the cycle.
"""
from __future__ import annotations

import pytest

from mailmind.ingestion.fetcher import _retry, _TRANSIENT_ERRORS


def test_retries_non_http_transient_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("connection reset")  # NOT an HttpError
        return "ok"

    assert _retry(flaky, retries=3, backoff=0) == "ok"
    assert calls["n"] == 3


def test_reraises_after_exhaustion():
    def always_fails():
        raise TimeoutError("upstream down")

    with pytest.raises(TimeoutError):
        _retry(always_fails, retries=2, backoff=0)


def test_non_transient_error_is_not_retried():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("programming bug, not transient")

    with pytest.raises(ValueError):
        _retry(boom, retries=3, backoff=0)
    assert calls["n"] == 1  # raised immediately, not retried


def test_transient_set_covers_transport_errors():
    names = {t.__name__ for t in _TRANSIENT_ERRORS}
    assert {"HttpError", "ConnectionError", "TimeoutError", "SSLError"} <= names
