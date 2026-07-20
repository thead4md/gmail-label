"""MailMind REST API — FastAPI backend for the React frontend.

This package is a thin HTTP wrapper: every route delegates straight into the
existing, already-tested mailmind.* modules (storage.queries, actions,
intelligence.feedback, processing.*). No business logic is duplicated or
reimplemented here — the safety invariants (dry-run default, no-delete,
confidence thresholds, the three-step reply gate) all live exactly where they
did for the Streamlit dashboard and are unchanged by this package's existence.
"""
