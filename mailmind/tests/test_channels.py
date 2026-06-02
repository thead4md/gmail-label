"""Tests for channel detection — English + Hungarian (intelligence/channels.py)."""
from __future__ import annotations

import pytest
from mailmind.intelligence.channels import detect_channel


# ===========================================================================
# ENGLISH — newsletter
# ===========================================================================

def test_en_unsubscribe_body_flags_newsletter():
    assert detect_channel("Weekly digest", "news@substack.com",
                          "Click here to unsubscribe from this newsletter.") == "newsletter"

def test_en_newsletter_sender_domain():
    assert detect_channel("Your weekly roundup", "hello@mailchimp.com",
                          "Hope you enjoy this week's content.") == "newsletter"

def test_en_opt_out_phrase_flags_newsletter():
    assert detect_channel("Updates", "updates@blog.com",
                          "If you wish to opt out of future emails click here.") == "newsletter"


# ===========================================================================
# HUNGARIAN — newsletter / hírlevél
# ===========================================================================

def test_hu_leiratkozas_flags_newsletter():
    """'leiratkozás' in body → newsletter."""
    assert detect_channel(
        subject="Heti összefoglaló",
        sender="info@webshop.hu",
        body_text="Ha nem szeretne több levelet kapni, kattintson ide a leiratkozáshoz.",
    ) == "newsletter"

def test_hu_hirlevel_in_sender_flags_newsletter():
    """'hírlevel' in sender address → newsletter."""
    assert detect_channel(
        subject="Május havi ajánlataink",
        sender="hirlevel@ceges.hu",
        body_text="Íme a legfrissebb ajánlataink.",
    ) == "newsletter"

def test_hu_nem_kerek_tobb_flags_newsletter():
    """'nem kérek több' phrase → newsletter."""
    assert detect_channel(
        subject="Cserkész Hírlevel",
        sender="info@cserkesz.hu",
        body_text="Ha nem kérek több levelet, kattintson a leiratkozás gombra.",
    ) == "newsletter"

def test_hu_kiiratkozas_flags_newsletter():
    """'kiiratkozás' stem in body → newsletter."""
    assert detect_channel(
        subject="Napi Digest",
        sender="digest@portal.hu",
        body_text="A kiiratkozáshoz kattintson ide.",
    ) == "newsletter"

def test_hu_ertesito_sender_flags_newsletter():
    """'értesítő' in sender → newsletter."""
    assert detect_channel(
        subject="Rendszeres értesítő",
        sender="ertesito@szolgaltato.hu",
        body_text="Ez az automatikusan generált értesítő.",
    ) == "newsletter"


# ===========================================================================
# ENGLISH — transactional
# ===========================================================================

def test_en_order_subject_is_transactional():
    assert detect_channel("Your order #1234 has shipped", "shipping@shop.com",
                          "Your package is on its way.") == "transactional"

def test_en_noreply_sender_is_transactional():
    assert detect_channel("Security alert", "no-reply@bank.com",
                          "A new sign-in was detected.") == "transactional"

def test_en_password_reset_is_transactional():
    assert detect_channel("Reset your password", "support@service.com",
                          "Click the link to reset your password.") == "transactional"

def test_en_invoice_subject_is_transactional():
    assert detect_channel("Invoice #456 from Acme Corp", "billing@acme.com",
                          "Please find your invoice attached.") == "transactional"


# ===========================================================================
# HUNGARIAN — transactional
# ===========================================================================

def test_hu_megrendeles_is_transactional():
    """'megrendelés' in subject → transactional."""
    assert detect_channel(
        subject="Megrendelés visszaigazolás — #HU-2024-0042",
        sender="rendeles@webshop.hu",
        body_text="Köszönjük megrendelését! A csomag 2–3 munkanapon belül érkezik.",
    ) == "transactional"

def test_hu_szamla_is_transactional():
    """'számla' in subject → transactional."""
    assert detect_channel(
        subject="Számla #2024/1234",
        sender="szamlazas@ceg.hu",
        body_text="Mellékletben küldjük a számlát.",
    ) == "transactional"

def test_hu_szamla_no_accent_is_transactional():
    """'szamla' without diacritics in subject → transactional."""
    assert detect_channel(
        subject="Szamla #2024/1234",
        sender="billing@ceg.hu",
        body_text="Mellekletben a szamla.",
    ) == "transactional"

def test_hu_szallitas_is_transactional():
    """'szállítás' in subject → transactional."""
    assert detect_channel(
        subject="Szállítás visszaigazolás",
        sender="info@futarszolgalat.hu",
        body_text="A csomagja úton van.",
    ) == "transactional"

def test_hu_fizetes_is_transactional():
    """'fizetés' in subject → transactional."""
    assert detect_channel(
        subject="Fizetés megerősítése",
        sender="no-reply@bank.hu",
        body_text="A tranzakció sikeresen befejeződött.",
    ) == "transactional"

def test_hu_jelszó_visszaallitas_is_transactional():
    """Password reset in Hungarian → transactional."""
    assert detect_channel(
        subject="Jelszó visszaállítás",
        sender="ugyfelszolgalat@platform.hu",
        body_text="Kattintson az alábbi linkre a jelszó visszaállításához.",
    ) == "transactional"

def test_hu_noreply_sender_hu_is_transactional():
    """'noreply@' Hungarian domain → transactional (sender pattern)."""
    assert detect_channel(
        subject="Fiók tevékenység értesítő",
        sender="noreply@magyarbank.hu",
        body_text="Új bejelentkezés észlelhető a fiókjában.",
    ) == "transactional"

def test_hu_ugyfelszolgalat_sender_is_transactional():
    """'ügyfélszolgálat' in sender → transactional."""
    assert detect_channel(
        subject="Visszaigazolás",
        sender="ugyfelszolgalat@szolgaltato.hu",
        body_text="Köszönjük megrendelését.",
    ) == "transactional"


# ===========================================================================
# ENGLISH — automated
# ===========================================================================

def test_en_build_failed_is_automated():
    assert detect_channel("[GitHub Actions] Build failed — main",
                          "noreply@github.com",
                          "Your workflow run failed.") == "automated"

def test_en_monitoring_alert_is_automated():
    assert detect_channel("[Alert] Server CPU > 90%", "alerts@datadog.com",
                          "CPU utilisation exceeded threshold.") == "automated"

def test_en_deploy_subject_is_automated():
    assert detect_channel("Deploy succeeded on production", "ci@circleci.com",
                          "Your deployment to production completed.") == "automated"


# ===========================================================================
# HUNGARIAN — automated
# ===========================================================================

def test_hu_rendszerhiba_is_automated():
    """'rendszerhiba' in subject → automated."""
    assert detect_channel(
        subject="Rendszerhiba értesítő",
        sender="alert@infrateam.hu",
        body_text="A szerveren kritikus hiba lépett fel.",
    ) == "automated"

def test_hu_telepites_sikertelen_is_automated():
    """'telepítés sikertelen' in subject → automated."""
    assert detect_channel(
        subject="Telepítés sikertelen — production",
        sender="ci@ceg.hu",
        body_text="A deploy pipeline megszakadt.",
    ) == "automated"

def test_hu_figyelmeztes_bracket_is_automated():
    """'[figyelmeztetés]' in subject → automated."""
    assert detect_channel(
        subject="[figyelmeztetés] Magas memóriahasználat",
        sender="monitor@hosting.hu",
        body_text="A szerver memóriahasználata 95% fölé emelkedett.",
    ) == "automated"


# ===========================================================================
# ENGLISH — marketing
# ===========================================================================

def test_en_sale_subject_is_marketing():
    assert detect_channel("50% off — today only!", "promos@shop.com",
                          "Shop now and save big.") == "marketing"

def test_en_flash_sale_is_marketing():
    assert detect_channel("Flash sale: limited time offer", "deals@company.com",
                          "Don't miss our exclusive deal.") == "marketing"


# ===========================================================================
# HUNGARIAN — marketing
# ===========================================================================

def test_hu_kedvezmeny_is_marketing():
    """'kedvezmény' in subject → marketing."""
    assert detect_channel(
        subject="30% kedvezmény csak ma!",
        sender="promo@webshop.hu",
        body_text="Használja fel az akciós kódunkat.",
    ) == "marketing"

def test_hu_akcio_is_marketing():
    """'akció' in subject → marketing."""
    assert detect_channel(
        subject="Nagy tavaszi akció — leárazás az egész boltban",
        sender="marketing@ceges.hu",
        body_text="Nézze meg ajánlatainkat.",
    ) == "marketing"

def test_hu_kulonleges_ajanlat_is_marketing():
    """'különleges ajánlat' in subject → marketing."""
    assert detect_channel(
        subject="Különleges ajánlat csak Önnek",
        sender="sales@ceg.hu",
        body_text="Exkluzív ajánlatunkra hívjuk fel figyelmét.",
    ) == "marketing"

def test_hu_korlátozott_ideig_is_marketing():
    """'korlátozott ideig' in subject → marketing."""
    assert detect_channel(
        subject="Korlátozott ideig érvényes ajánlat",
        sender="promo@kereskedo.hu",
        body_text="Sietsen, az akció hamarosan lejár!",
    ) == "marketing"


# ===========================================================================
# Team detection
# ===========================================================================

def test_hu_same_domain_is_team():
    """Same org domain (Hungarian company) → team."""
    assert detect_channel(
        subject="Csapatértekezlet holnap 10:00",
        sender="kovacs.peter@ceg.hu",
        body_text="Kérem, hogy mindenki legyen jelen az értekezleten.",
        user_domain="ceg.hu",
    ) == "team"

def test_different_domain_not_team():
    assert detect_channel("Hello", "bob@other.com", "Just checking in.",
                          user_domain="company.com") != "team"

def test_no_user_domain_skips_team():
    ch = detect_channel("Meeting", "alice@company.hu", "Can we meet?", user_domain=None)
    assert ch in ("personal", "unknown")


# ===========================================================================
# Personal / unknown
# ===========================================================================

def test_hu_personal_human_email():
    """Ordinary Hungarian one-to-one email → personal."""
    assert detect_channel(
        subject="Hogy vagy?",
        sender="barát@gmail.com",
        body_text="Szia, mikor találkozunk legközelebb?",
    ) == "personal"

def test_en_personal_human_email():
    assert detect_channel("Are you free for lunch?", "friend@gmail.com",
                          "Hey, want to grab lunch on Friday?") == "personal"

def test_empty_fields_returns_unknown_or_personal():
    ch = detect_channel(None, None, None)
    assert ch in ("unknown", "personal")

def test_priority_automated_over_transactional():
    """[Alert] should be automated even when sender looks transactional."""
    assert detect_channel(
        subject="[Alert] Payment gateway error",
        sender="noreply@payments.com",
        body_text="Payment service returned 503.",
    ) == "automated"

def test_hu_priority_automated_over_transactional():
    """Hungarian [figyelmeztetés] beats transactional sender."""
    assert detect_channel(
        subject="[figyelmeztetés] Fizetési átjáró hiba",
        sender="noreply@fizetes.hu",
        body_text="A fizetési szolgáltatás nem elérhető.",
    ) == "automated"
