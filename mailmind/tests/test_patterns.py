"""Tests for canonical detection patterns (intelligence/patterns.py)."""
from __future__ import annotations

import pytest
from mailmind.intelligence.patterns import (
    UNSUBSCRIBE_RE,
    CALENDAR_RE,
    FINANCE_RE,
    FINANCE_DOMAINS,
)


class TestUnsubscribeRE:
    """Test UNSUBSCRIBE_RE canonical pattern."""

    def test_en_unsubscribe_variants(self):
        """Test English unsubscribe keywords."""
        assert UNSUBSCRIBE_RE.search("unsubscribe here")
        assert UNSUBSCRIBE_RE.search("Click here to unsubscribe")
        assert UNSUBSCRIBE_RE.search("opt-out of this list")
        assert UNSUBSCRIBE_RE.search("opt out of this list")
        assert UNSUBSCRIBE_RE.search("manage your preference")
        assert UNSUBSCRIBE_RE.search("manage email preference")
        assert UNSUBSCRIBE_RE.search("view in browser")

    def test_en_no_false_positives(self):
        """Test English text without unsubscribe signals."""
        assert not UNSUBSCRIBE_RE.search("Thanks for your order")
        assert not UNSUBSCRIBE_RE.search("Hello, just checking in")

    def test_hu_leiratkozas(self):
        """Test Hungarian unsubscribe: 'leiratkozás'."""
        assert UNSUBSCRIBE_RE.search("Ha nem szeretne több levelet, kattintson ide a leiratkozáshoz.")

    def test_hu_kiiratkozas(self):
        """Test Hungarian unsubscribe: 'kiiratkozás'."""
        assert UNSUBSCRIBE_RE.search("A kiiratkozáshoz kattintson ide.")

    def test_hu_nem_kerek_tobb(self):
        """Test Hungarian unsubscribe: 'nem kérek több'."""
        assert UNSUBSCRIBE_RE.search("Ha nem kérek több levelet, kattintson a gombra.")

    def test_hu_hirlevel(self):
        """Test Hungarian newsletter: 'hírlevél'."""
        assert UNSUBSCRIBE_RE.search("Feliratkozott erre a hírlevélre.")

    def test_hu_feliratkozas_visszavon(self):
        """Test Hungarian unsubscribe: 'feliratkozás visszavonása'."""
        assert UNSUBSCRIBE_RE.search("A feliratkozás visszavonásához kattintson.")


class TestCalendarRE:
    """Test CALENDAR_RE canonical pattern."""

    def test_en_invitation(self):
        """Test English calendar: 'invitation'."""
        assert CALENDAR_RE.search("You are invited to a meeting")
        assert CALENDAR_RE.search("Meeting invitation: Project review")

    def test_en_calendar_ical(self):
        """Test English calendar keywords: 'calendar', 'event', 'meeting'."""
        assert CALENDAR_RE.search("Calendar notification")
        assert CALENDAR_RE.search("Event: Team sync")
        assert CALENDAR_RE.search("Meeting accepted")

    def test_en_google_calendar(self):
        """Test Google Calendar sender domain."""
        assert CALENDAR_RE.search("calendar-notification@google.com")
        assert CALENDAR_RE.search("test@resource.calendar.google.com")

    def test_en_declined_accepted(self):
        """Test English calendar: 'declined', 'accepted' with event/meeting."""
        assert CALENDAR_RE.search("declined the event")
        assert CALENDAR_RE.search("accepted the meeting")

    def test_en_no_false_positives(self):
        """Test English text without calendar signals."""
        assert not CALENDAR_RE.search("Thanks for your support today")

    def test_hu_naptar(self):
        """Test Hungarian calendar: 'naptár'."""
        assert CALENDAR_RE.search("Naptár értesítés érkezett")

    def test_hu_esemeny(self):
        """Test Hungarian event: 'esemény'."""
        assert CALENDAR_RE.search("Esemény meghívó: csapattalálkozó")

    def test_hu_meghivo(self):
        """Test Hungarian invite: 'meghívó'."""
        assert CALENDAR_RE.search("Meghívót kaptál egy eseményre")


class TestFinanceRE:
    """Test FINANCE_RE canonical pattern."""

    def test_en_payment(self):
        """Test English finance: 'payment'."""
        assert FINANCE_RE.search("Payment received")
        assert FINANCE_RE.search("Your payment is due")

    def test_en_invoice(self):
        """Test English finance: 'invoice'."""
        assert FINANCE_RE.search("Your invoice is attached")
        assert FINANCE_RE.search("Invoice #1234")

    def test_en_receipt(self):
        """Test English finance: 'receipt'."""
        assert FINANCE_RE.search("Receipt for your purchase")

    def test_en_transaction(self):
        """Test English finance: 'transaction'."""
        assert FINANCE_RE.search("Transaction completed")

    def test_en_bill(self):
        """Test English finance: 'bill'."""
        assert FINANCE_RE.search("Your monthly bill")

    def test_en_charge(self):
        """Test English finance: 'charge'."""
        assert FINANCE_RE.search("Charge confirmation")

    def test_en_no_false_positives(self):
        """Test English text without finance signals."""
        assert not FINANCE_RE.search("Thanks for your message")


class TestFinanceDomains:
    """Test FINANCE_DOMAINS set."""

    def test_finance_domains_no_duplicates(self):
        """Test that FINANCE_DOMAINS has no duplicate entries."""
        domains_list = list(FINANCE_DOMAINS)
        unique_domains = set(domains_list)
        assert len(domains_list) == len(unique_domains), (
            f"FINANCE_DOMAINS contains duplicates: {domains_list}"
        )

    def test_finance_domains_no_deprecated(self):
        """Test that deprecated 'transferwise.com' is not in FINANCE_DOMAINS."""
        assert 'transferwise.com' not in FINANCE_DOMAINS

    def test_finance_domains_canonical(self):
        """Test that expected domains are present."""
        expected = {
            'paypal.com',
            'stripe.com',
            'revolut.com',
            'otp.hu',
            'wise.com',
            'n26.com',
        }
        assert FINANCE_DOMAINS == expected
