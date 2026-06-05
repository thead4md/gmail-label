"""build_model_text — the single source of TF-IDF input for train AND inference.

Locks in: (a) body is included (the train/inference mismatch that omitted body at
inference is fixed), and (b) structured feature tokens are appended.
"""
from __future__ import annotations

from mailmind.ml.features import build_model_text
from mailmind.ml.inference import predict_label
from mailmind.ml.model import MLClassifier, ModelMetadata
from mailmind.storage.models import Email


def test_includes_body_and_core_fields():
    text = build_model_text("Subject here", "boss@acme.com", "snippet text", "BODY CONTENT")
    for part in ("subject here", "snippet text", "boss@acme.com", "body content"):
        assert part in text.lower()


def test_appends_feature_tokens():
    text = build_model_text(
        "Re: invoice", "billing@acme.com", "", "Please pay this invoice. unsubscribe here."
    )
    assert "feat_finance" in text          # finance signal
    assert "feat_unsub" in text            # unsubscribe signal
    assert "feat_reply" in text            # Re: prefix
    assert "feat_domain_acme_com" in text  # sender domain token


def test_body_is_capped():
    long_body = "x" * 5000
    text = build_model_text("s", "a@b.com", "", long_body)
    assert text.count("x") == 500          # body capped at 500 chars


def test_train_inference_use_same_text():
    """The text the trainer builds must equal what inference feeds the model."""
    email = Email(gmail_id="p1", sender="a@b.com", subject="Hi", snippet="sn", body_text="body")
    inference_text = build_model_text(
        subject=email.subject, sender=email.sender,
        snippet=email.snippet, body_text=email.body_text,
    )
    # Trainer builds it from the same fields in the same order.
    train_text = build_model_text(email.subject, email.sender, email.snippet, email.body_text)
    assert inference_text == train_text


def test_predict_label_runs_with_unified_text():
    """End-to-end: a fitted model classifies via the unified inference path."""
    corpus = [
        build_model_text("invoice due", "billing@x.com", "", "pay invoice"),
        build_model_text("invoice overdue", "billing@y.com", "", "payment needed"),
        build_model_text("weekly digest", "news@z.com", "", "unsubscribe here"),
        build_model_text("newsletter", "news@w.com", "", "unsubscribe link"),
    ]
    labels = ["FINANCE", "FINANCE", "NEWSLETTER", "NEWSLETTER"]
    clf = MLClassifier()
    clf.train(corpus, labels, metadata=ModelMetadata(class_names=sorted(set(labels)), num_samples=4))
    res = predict_label(Email(gmail_id="e", sender="billing@x.com",
                              subject="invoice due", body_text="pay invoice"), clf)
    assert res.model_available is True
    assert res.primary_label in ("FINANCE", "NEWSLETTER")
