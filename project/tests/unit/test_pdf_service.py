"""Unit tests for the reportlab PDF service (v1.3)."""

from datetime import date

import pytest

from billing.services.pdf_service import (
    generate_payment_receipt,
    generate_quarterly_summary,
    generate_tax_certificate,
)

pytestmark = pytest.mark.django_db


def _looks_like_pdf(data: bytes) -> bool:
    """A PDF file always starts with `%PDF-` and ends with `%%EOF`."""
    return data.startswith(b"%PDF-") and b"%%EOF" in data[-200:]


class TestGeneratePaymentReceipt:
    def test_returns_pdf_bytes(self, pending_payment):
        pending_payment.payment_status = "completed"
        pending_payment.payment_date = date.today()
        pending_payment.save()
        pdf = generate_payment_receipt(pending_payment)
        assert isinstance(pdf, bytes)
        assert _looks_like_pdf(pdf)

    def test_handles_missing_payment_date(self, pending_payment):
        pdf = generate_payment_receipt(pending_payment)
        assert _looks_like_pdf(pdf)

    def test_handles_missing_parent(self, adult_student, active_enrollment):
        from decimal import Decimal

        from billing.models import Payment

        p = Payment.objects.create(
            student=adult_student,
            parent=None,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("60.00"),
            payment_status="completed",
            due_date=date(2026, 1, 1),
            payment_date=date(2026, 1, 1),
            concept="Test payment",
        )
        pdf = generate_payment_receipt(p)
        assert _looks_like_pdf(pdf)


class TestGenerateQuarterlySummary:
    def test_returns_pdf_bytes(self, student, pending_payment, completed_payment):
        pdf = generate_quarterly_summary(student, [pending_payment, completed_payment], "Q1 2026")
        assert _looks_like_pdf(pdf)

    def test_empty_payments(self, student):
        pdf = generate_quarterly_summary(student, [], "Q4 2025")
        assert _looks_like_pdf(pdf)


class TestGenerateTaxCertificate:
    def test_no_payments_still_returns_pdf(self, parent):
        pdf = generate_tax_certificate(parent, 2025)
        assert _looks_like_pdf(pdf)

    def test_with_completed_payments(self, parent, completed_payment):
        year = completed_payment.payment_date.year
        pdf = generate_tax_certificate(parent, year)
        assert _looks_like_pdf(pdf)
        # PDF should be non-trivial in size (has payment data)
        assert len(pdf) > 1000
