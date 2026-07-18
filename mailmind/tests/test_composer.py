"""Phase 3B: MIME composer tests. Pure logic, zero network/DB calls."""
from __future__ import annotations

import base64
from email import message_from_bytes
from email.header import decode_header

from mailmind.compose.composer import (
    build_reply_mime,
    build_new_message_mime,
    quote_original_body,
)


def _decode(raw_b64url: str):
    raw_bytes = base64.urlsafe_b64decode(raw_b64url.encode("ascii"))
    return message_from_bytes(raw_bytes)


def _decoded_subject(msg) -> str:
    parts = decode_header(msg["Subject"])
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "ascii"))
        else:
            out.append(text)
    return "".join(out)


def _plain_and_html_parts(msg):
    plain = None
    html_body = None
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            plain = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")
        elif part.get_content_type() == "text/html":
            html_body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")
    return plain, html_body


def test_plain_ascii_reply_round_trip():
    raw = build_reply_mime(
        to_addr="alice@example.com",
        subject="Meeting tomorrow",
        body_text="Sounds good, see you then.",
        in_reply_to_message_id="abc123@mail.gmail.com",
    )
    msg = _decode(raw)

    assert msg["To"] == "alice@example.com"
    assert _decoded_subject(msg) == "Re: Meeting tomorrow"
    assert msg["In-Reply-To"] == "<abc123@mail.gmail.com>"
    assert msg["References"] == "<abc123@mail.gmail.com>"
    assert msg.get_content_type() == "multipart/alternative"

    plain, html_body = _plain_and_html_parts(msg)
    assert plain.strip() == "Sounds good, see you then."
    assert "Sounds good, see you then." in html_body


def test_subject_not_double_prefixed_case_insensitive():
    raw = build_reply_mime(
        to_addr="bob@example.com",
        subject="RE: Already replied subject",
        body_text="ok",
    )
    msg = _decode(raw)
    assert _decoded_subject(msg) == "RE: Already replied subject"


def test_non_ascii_subject_and_body_hungarian():
    raw = build_reply_mime(
        to_addr="cserkesz@example.com",
        subject="Üdvözlet Cserkész csapat",
        body_text="Szia Cserkész csapat!\nÜdvözlettel,\nÁdám",
        in_reply_to_message_id="<hu123@mail.gmail.com>",
    )
    msg = _decode(raw)

    assert _decoded_subject(msg) == "Re: Üdvözlet Cserkész csapat"

    plain, html_body = _plain_and_html_parts(msg)
    assert "Szia Cserkész csapat!" in plain
    assert "Ádám" in plain
    # HTML-escaped body should still contain the readable (unescaped by html.unescape
    # isn't needed here since Hungarian accented chars aren't HTML-special) text and
    # should have replaced newlines with <br>.
    assert "Cserkész" in html_body
    assert "<br>" in html_body


def test_reply_chains_existing_references_not_overwrite():
    existing_references = "<orig1@mail.gmail.com> <orig2@mail.gmail.com>"
    raw = build_reply_mime(
        to_addr="carol@example.com",
        subject="Thread subject",
        body_text="Reply body",
        in_reply_to_message_id="orig3@mail.gmail.com",
        references=existing_references,
    )
    msg = _decode(raw)

    assert msg["In-Reply-To"] == "<orig3@mail.gmail.com>"
    # Long headers may be RFC 2822 folded (continuation lines prefixed with a single
    # space), so normalize whitespace before comparing the logical value.
    assert " ".join(msg["References"].split()) == (
        "<orig1@mail.gmail.com> <orig2@mail.gmail.com> <orig3@mail.gmail.com>"
    )


def test_reply_without_in_reply_to_fresh_thread_edge_case():
    raw = build_reply_mime(
        to_addr="dave@example.com",
        subject="No threading info available",
        body_text="body",
    )
    msg = _decode(raw)
    assert msg["In-Reply-To"] is None
    assert msg["References"] is None
    assert _decoded_subject(msg) == "Re: No threading info available"


def test_reply_with_cc_addr():
    raw = build_reply_mime(
        to_addr="eve@example.com",
        subject="cc test",
        body_text="body",
        cc_addr="frank@example.com",
    )
    msg = _decode(raw)
    assert msg["Cc"] == "frank@example.com"


def test_from_addr_unset_by_default_for_gmail_autofill():
    raw = build_reply_mime(
        to_addr="alice@example.com",
        subject="No from set",
        body_text="body",
    )
    msg = _decode(raw)
    assert msg["From"] is None


def test_from_addr_settable_for_testability():
    raw = build_new_message_mime(
        to_addr="alice@example.com",
        subject="Explicit from",
        body_text="body",
        from_addr="me@example.com",
    )
    msg = _decode(raw)
    assert msg["From"] == "me@example.com"


def test_new_message_compose_no_threading_headers():
    raw = build_new_message_mime(
        to_addr="george@example.com",
        subject="Fresh compose",
        body_text="Hello there.\nSecond line.",
    )
    msg = _decode(raw)

    assert msg["In-Reply-To"] is None
    assert msg["References"] is None
    assert _decoded_subject(msg) == "Fresh compose"

    plain, html_body = _plain_and_html_parts(msg)
    assert "Hello there." in plain
    assert "Second line." in plain
    assert "<br>" in html_body


def test_html_special_chars_escaped():
    raw = build_new_message_mime(
        to_addr="x@example.com",
        subject="html escape test",
        body_text="Is 5 < 10 && 10 > 3? Yes & confirmed.",
    )
    msg = _decode(raw)
    _plain, html_body = _plain_and_html_parts(msg)
    assert "&lt;" in html_body
    assert "&gt;" in html_body
    assert "&amp;" in html_body
    # raw unescaped special chars should not leak into the html part literally
    assert "5 < 10" not in html_body


def test_quote_original_body_multiline_formatting():
    quoted = quote_original_body(
        original_sender="Alice <alice@example.com>",
        original_date_ts=1_700_000_000,
        original_body_text="Line one.\nLine two.\nLine three.",
    )
    lines = quoted.split("\n")
    assert lines[0].startswith("On ")
    assert "Alice <alice@example.com> wrote:" in lines[0]
    assert lines[1] == "> Line one."
    assert lines[2] == "> Line two."
    assert lines[3] == "> Line three."


def test_quote_original_body_single_line():
    quoted = quote_original_body(
        original_sender="bob@example.com",
        original_date_ts=1_700_000_000,
        original_body_text="Just one line.",
    )
    assert quoted.endswith("> Just one line.")


def test_round_trip_base64url_decode_subject_body_headers_survive():
    raw = build_reply_mime(
        to_addr="roundtrip@example.com",
        subject="Round trip check",
        body_text="Verify this survives encode/decode intact.",
        in_reply_to_message_id="rt1@mail.gmail.com",
        references="<rt0@mail.gmail.com>",
        cc_addr="cc@example.com",
    )
    # Confirm it's valid urlsafe base64 (no padding errors) and decodes cleanly.
    raw_bytes = base64.urlsafe_b64decode(raw.encode("ascii"))
    msg = message_from_bytes(raw_bytes)

    assert msg["To"] == "roundtrip@example.com"
    assert msg["Cc"] == "cc@example.com"
    assert _decoded_subject(msg) == "Re: Round trip check"
    assert msg["In-Reply-To"] == "<rt1@mail.gmail.com>"
    assert " ".join(msg["References"].split()) == "<rt0@mail.gmail.com> <rt1@mail.gmail.com>"

    plain, _html_body = _plain_and_html_parts(msg)
    assert plain.strip() == "Verify this survives encode/decode intact."
