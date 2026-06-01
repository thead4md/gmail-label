from mailmind.intelligence.thread_analyzer import ThreadAnalyzer, ThreadContext
from mailmind.storage.models import Email


def test_thread_analyzer_simple_question():
    e = Email(gmail_id='m1', subject='Question', snippet='', body_text='Can you send the report?')
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.reply_needed is True
    assert ctx.open_question_detected is True


def test_thread_analyzer_waiting_phrase():
    e = Email(gmail_id='m2', subject='FW: update', snippet='', body_text="We'll update you next week.")
    ctx = ThreadAnalyzer.analyze(e)
    assert ctx.waiting_on_other_party is True
    assert ctx.reply_needed is False

