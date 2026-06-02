"""Tests for ThreadAnalyzer — English + Hungarian."""
from __future__ import annotations

from mailmind.intelligence.thread_analyzer import ThreadAnalyzer, ThreadContext
from mailmind.storage.models import Email


# ---------------------------------------------------------------------------
# English — existing baseline
# ---------------------------------------------------------------------------

def test_en_simple_question_reply_needed():
    e = Email(gmail_id="m1", subject="Question", snippet="",
              body_text="Can you send the report?")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is True
    assert ctx.open_question_detected is True


def test_en_waiting_phrase():
    e = Email(gmail_id="m2", subject="FW: update", snippet="",
              body_text="We'll update you next week.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.waiting_on_other_party is True
    assert ctx.reply_needed is False


def test_en_question_mark_triggers_reply_needed():
    e = Email(gmail_id="m3", subject="Hi", snippet="",
              body_text="Are you available tomorrow?")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is True


def test_en_no_signals_not_reply_needed():
    e = Email(gmail_id="m4", subject="FYI", snippet="",
              body_text="Just sending you this for your records.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is False
    assert ctx.waiting_on_other_party is False


# ---------------------------------------------------------------------------
# Hungarian — reply needed
# ---------------------------------------------------------------------------

def test_hu_kerem_jelezze_reply_needed():
    e = Email(gmail_id="hu1", subject="Találkozó", snippet="",
              body_text="Kérem jelezze, hogy tud-e részt venni holnap az értekezleten.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is True


def test_hu_tudna_kuldeni_reply_needed():
    e = Email(gmail_id="hu2", subject="Dokumentum", snippet="",
              body_text="Tudna küldeni a szerződés végleges változatát?")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is True


def test_hu_kerem_visszaigazolas_reply_needed():
    e = Email(gmail_id="hu3", subject="Rendezvény", snippet="",
              body_text="Kérem visszaigazolását a részvétellel kapcsolatban.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is True


def test_hu_varom_valaszat_reply_needed():
    e = Email(gmail_id="hu4", subject="Ajánlat", snippet="",
              body_text="Várom mielőbbi válaszát az ajánlattal kapcsolatban.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is True


def test_hu_valaszoljon_reply_needed():
    e = Email(gmail_id="hu5", subject="Sürgős", snippet="",
              body_text="Kérem válaszoljon mielőbb, mert holnap döntést kell hozni.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is True


def test_hu_kerdesem_van_reply_needed():
    e = Email(gmail_id="hu6", subject="Kérdés", snippet="",
              body_text="Kérdésem van az alábbi tételekkel kapcsolatban.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is True


def test_hu_question_mark_triggers_reply():
    e = Email(gmail_id="hu7", subject="Elérhető vagy?", snippet="",
              body_text="Szia! Szabad vagy holnap délután?")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is True
    assert ctx.open_question_detected is True


def test_hu_kerem_erositsen_reply_needed():
    e = Email(gmail_id="hu8", subject="Visszaigazolás", snippet="",
              body_text="Kérem erősítse meg a foglalást.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is True


def test_hu_ertesitsen_reply_needed():
    e = Email(gmail_id="hu9", subject="Eredmény", snippet="",
              body_text="Kérem értesítsen az eredményről.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is True


def test_hu_mi_a_velemenye_reply_needed():
    e = Email(gmail_id="hu10", subject="Tervezet", snippet="",
              body_text="Mi a véleménye az alábbi javaslatról?")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is True


# ---------------------------------------------------------------------------
# Hungarian — waiting on other party
# ---------------------------------------------------------------------------

def test_hu_visszajelzunk_waiting():
    e = Email(gmail_id="hw1", subject="Feldolgozás alatt", snippet="",
              body_text="Köszönjük megkeresését. Hamarosan visszajelzünk.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.waiting_on_other_party is True
    assert ctx.reply_needed is False


def test_hu_ertesiteni_fogjuk_waiting():
    e = Email(gmail_id="hw2", subject="Kérelem", snippet="",
              body_text="Kérelmét rögzítettük. Hamarosan értesíteni fogjuk az eredményről.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.waiting_on_other_party is True


def test_hu_felkeressjuk_waiting():
    e = Email(gmail_id="hw3", subject="Visszahívás", snippet="",
              body_text="Munkatársunk hamarosan felkeressük Önt telefonon.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.waiting_on_other_party is True


def test_hu_dolgozunk_rajta_waiting():
    e = Email(gmail_id="hw4", subject="Státusz", snippet="",
              body_text="Köszönjük türelmét, dolgozunk rajta és hamarosan válaszolunk.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.waiting_on_other_party is True


def test_hu_folyamatban_van_waiting():
    e = Email(gmail_id="hw5", subject="Igénylés", snippet="",
              body_text="Az igénylés elbírálása folyamatban van.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.waiting_on_other_party is True


# ---------------------------------------------------------------------------
# Thread detection — Hungarian subject prefixes
# ---------------------------------------------------------------------------

def test_hu_valasz_prefix_is_thread():
    e = Email(gmail_id="ht1", subject="Válasz: Megbeszélés holnap", snippet="",
              body_text="Igen, részt tudok venni.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.is_thread is True


def test_hu_re_prefix_is_thread():
    e = Email(gmail_id="ht2", subject="Re: Projekt határidő", snippet="",
              body_text="Oké, megcsinálom péntekig.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.is_thread is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_no_body_text_safe():
    e = Email(gmail_id="edge1", subject="Üres", snippet="", body_text=None)
    ctx = ThreadAnalyzer.analyze(e)
    assert isinstance(ctx, ThreadContext)


def test_no_signals_notification_email():
    e = Email(gmail_id="edge2", subject="Megrendelés visszaigazolás", snippet="",
              body_text="Köszönjük megrendelését. A csomag 3–5 munkanapon belül érkezik.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is False
    assert ctx.waiting_on_other_party is False
