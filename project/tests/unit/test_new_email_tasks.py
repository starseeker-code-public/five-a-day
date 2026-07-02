"""Tests for the two new email tasks added during the review pass:
- comms.tasks.send_parent_magic_link_task  (v1.9 async fix)
- comms.tasks.send_payment_receipt_email_task  (v1.11 receipt email)
"""

from unittest.mock import patch

import pytest

from comms.tasks import send_parent_magic_link_task, send_payment_receipt_email_task

pytestmark = pytest.mark.django_db


class TestSendParentMagicLinkTask:
    def test_returns_error_when_parent_missing(self):
        result = send_parent_magic_link_task.run(999_999, "http://x/")
        assert result["status"] == "error"

    def test_skipped_when_parent_has_no_email(self, parent):
        parent.email = ""
        parent.save()
        result = send_parent_magic_link_task.run(parent.id, "http://x/")
        assert result["status"] == "skipped"

    def test_calls_email_service_with_expected_template(self, parent):
        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
            result = send_parent_magic_link_task.run(parent.id, "http://x/parent/login/abc/")
        assert result["status"] == "success"
        _, kwargs = mock_send.call_args
        assert kwargs["template_name"] == "parent_magic_link"
        assert kwargs["recipients"] == parent.email
        assert kwargs["context"]["link"] == "http://x/parent/login/abc/"
        assert kwargs["context"]["parent_name"] == parent.first_name
        assert kwargs["context"]["expires_minutes"] == 30

    def test_failure_reported(self, parent):
        with patch("comms.services.email_service.EmailService.send_email", return_value=False):
            result = send_parent_magic_link_task.run(parent.id, "http://x/")
        assert result["status"] == "failed"


class TestSendPaymentReceiptEmailTask:
    def test_returns_error_when_payment_missing(self):
        result = send_payment_receipt_email_task.run(999_999)
        assert result["status"] == "error"

    def test_skipped_when_no_recipient(self, completed_payment):
        # Wipe both parent.email and student.email so there's no recipient
        if completed_payment.parent:
            completed_payment.parent.email = ""
            completed_payment.parent.save()
        completed_payment.student.email = ""
        completed_payment.student.save()

        with patch("billing.services.pdf_service.generate_payment_receipt", return_value=b"%PDF-fake"):
            result = send_payment_receipt_email_task.run(completed_payment.id)
        assert result["status"] == "skipped"

    def test_sends_pdf_receipt(self, completed_payment):
        with patch("billing.services.pdf_service.generate_payment_receipt", return_value=b"%PDF-1.4\nhi\n%%EOF"):
            with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
                result = send_payment_receipt_email_task.run(completed_payment.id)

        assert result["status"] == "success"
        _, kwargs = mock_send.call_args
        assert kwargs["template_name"] == "payment_receipt"
        # PDF attached as (filename, bytes, mimetype)
        attachments = kwargs["attachments"]
        assert len(attachments) == 1
        assert attachments[0][0].endswith(".pdf")
        assert attachments[0][2] == "application/pdf"
        assert attachments[0][1].startswith(b"%PDF")

    def test_falls_back_to_student_email_when_no_parent(self, adult_student, active_enrollment):
        """Adult students have no parent — recipient must be the student's own email."""
        from datetime import date
        from decimal import Decimal

        from billing.models import Payment

        payment = Payment.objects.create(
            student=adult_student,
            parent=None,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("60.00"),
            payment_status="completed",
            due_date=date.today(),
            payment_date=date.today(),
            concept="Test",
        )
        with patch("billing.services.pdf_service.generate_payment_receipt", return_value=b"%PDF-fake"):
            with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
                result = send_payment_receipt_email_task.run(payment.id)
        assert result["status"] == "success"
        _, kwargs = mock_send.call_args
        assert kwargs["recipients"] == adult_student.email
