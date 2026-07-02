"""Integration test for the payment-receipt PDF endpoint (v1.3)."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestPaymentReceiptPdf:
    def test_returns_pdf(self, authenticated_client, pending_payment):
        response = authenticated_client.get(reverse("payment_receipt_pdf", args=[pending_payment.id]))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content.startswith(b"%PDF-")
        assert 'attachment; filename="recibo-' in response["Content-Disposition"]

    def test_404_for_missing_payment(self, authenticated_client):
        response = authenticated_client.get(reverse("payment_receipt_pdf", args=[999_999]))
        assert response.status_code == 404
