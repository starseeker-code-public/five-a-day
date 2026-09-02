"""Unit tests for the reportlab PDF service (v1.3)."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from billing.models import Payment
from billing.services.pdf_service import (
    generate_payment_receipt,
    generate_quarterly_summary,
    generate_student_payment_history,
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


class TestTableCellsAreNotDoubleEscaped:
    """reportlab draws a plain `Table` cell with `drawString` — no mini-HTML
    parsing — so running the concept through `_md()` printed the entity itself
    and "Clases & material" came out as "Clases &amp; material".

    Only text reaching a `Paragraph` needs escaping. `generate_quarterly_summary`
    and `generate_tax_certificate` already wrote this same field raw, which is
    what made the payment history the odd one out.
    """

    def _payment(self, student, parent, enrollment, concept):
        return Payment.objects.create(
            student=student,
            parent=parent,
            enrollment=enrollment,
            payment_type="monthly",
            amount=Decimal("54.00"),
            payment_status="pending",
            due_date=date(2025, 10, 31),
            concept=concept,
        )

    def test_an_ampersand_reaches_the_table_verbatim(self, student, parent, active_enrollment):
        from reportlab.platypus import Table as RealTable

        payment = self._payment(student, parent, active_enrollment, "Clases extra & material")
        captured = []

        def spy(rows, *args, **kwargs):
            captured.append(rows)
            return RealTable(rows, *args, **kwargs)

        with patch("billing.services.pdf_service.Table", side_effect=spy):
            generate_student_payment_history(student, [payment])

        concepts = [row[0] for row in captured[0]]
        assert "Clases extra & material" in concepts
        assert "Clases extra &amp; material" not in concepts

    def test_markup_characters_no_longer_reach_a_parser(self, student, parent, active_enrollment):
        payment = self._payment(student, parent, active_enrollment, "Nivel <B2> & repaso")

        assert generate_student_payment_history(student, [payment])[:4] == b"%PDF"
