"""Pass 10B: action item + deadline extraction (EN + HU)."""
from __future__ import annotations

from mailmind.intelligence.thread_analyzer import ThreadAnalyzer
from mailmind.storage.models import Email


def _ctx(body, subject="Test"):
    return ThreadAnalyzer.analyze(Email(gmail_id="x", subject=subject, body_text=body))


def test_en_action_item_detected():
    ctx = _ctx("Hi. Please review the attached contract. Thanks.")
    assert any("review" in a.lower() for a in ctx.action_items)


def test_hu_action_item_detected():
    ctx = _ctx("Szia. Kérem küldje el a végleges szerződést. Köszönöm.")
    assert len(ctx.action_items) >= 1


def test_en_deadline_detected():
    ctx = _ctx("Please send the report by Friday.")
    assert len(ctx.deadlines) >= 1


def test_hu_deadline_ig_suffix():
    ctx = _ctx("A dokumentumot péntekig kérjük beküldeni.")
    assert len(ctx.deadlines) >= 1


def test_hu_deadline_dotted_date():
    ctx = _ctx("Határidő: 2026.06.15. — kérjük tartsák be.")
    assert len(ctx.deadlines) >= 1


def test_no_false_positive_fyi():
    ctx = _ctx("Just sharing this for your records. No action needed.")
    assert ctx.action_items == []
    assert ctx.deadlines == []


def test_action_items_capped_at_5():
    body = " ".join(f"Please review item {i}." for i in range(10))
    ctx = _ctx(body)
    assert len(ctx.action_items) <= 5


def test_explainer_passes_action_items_through():
    from mailmind.intelligence.explainer import build_reason_payload
    import tempfile
    import pathlib
    from mailmind.storage.database import Database
    from mailmind.storage.models import Prediction, Email

    with tempfile.TemporaryDirectory() as d:
        db = Database(pathlib.Path(d) / "t.db")
        db.insert_email(Email(gmail_id="e1", sender="a@b.com", subject="S"))
        pred = Prediction(
            email_gmail_id="e1",
            model="rules",
            labels=["WORK"],
            priority_score=70,
            primary_label="WORK",
        )
        payload = build_reason_payload(
            db,
            pred,
            thread_context={
                "action_items": ["Please sign"],
                "deadlines": ["by Friday"],
            },
        )
        assert payload.action_items == ["Please sign"]
        assert payload.deadlines == ["by Friday"]
