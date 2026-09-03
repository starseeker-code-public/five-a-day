"""Tests for two email tasks added during the review pass:
- comms.tasks.send_parent_temporary_password_task  (v1.9 async fix; portal
  credential email since v1.27)
- comms.tasks.send_payment_receipt_email_task  (v1.11 receipt email)
"""

from unittest.mock import patch

import pytest

from comms.tasks import send_parent_temporary_password_task, send_payment_receipt_email_task

pytestmark = pytest.mark.django_db

LOGIN_URL = "http://x/parent/login/"


class TestSendParentTemporaryPasswordTask:
    def test_returns_error_when_parent_missing(self):
        result = send_parent_temporary_password_task.run(999_999, LOGIN_URL)
        assert result["status"] == "error"

    def test_skipped_when_parent_has_no_email(self, parent):
        parent.email = ""
        parent.save()
        result = send_parent_temporary_password_task.run(parent.id, LOGIN_URL)
        assert result["status"] == "skipped"

    def test_calls_email_service_with_expected_template(self, parent):
        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
            result = send_parent_temporary_password_task.run(parent.id, LOGIN_URL)
        assert result["status"] == "success"
        _, kwargs = mock_send.call_args
        assert kwargs["template_name"] == "parent_temporary_password"
        assert kwargs["recipients"] == parent.email
        assert kwargs["context"]["login_url"] == LOGIN_URL
        assert kwargs["context"]["parent_name"] == parent.first_name
        assert kwargs["context"]["reset"] is False

    def test_the_emailed_password_is_the_one_that_was_stored(self, parent):
        """The plaintext exists only in the email; the row keeps a hash."""
        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
            send_parent_temporary_password_task.run(parent.id, LOGIN_URL)

        emailed = mock_send.call_args[1]["context"]["temporary_password"]
        parent.refresh_from_db()
        assert parent.temporary_password != emailed, "the plaintext must never be persisted"
        assert parent.authenticate_portal(emailed) == "temporary"

    def test_the_password_is_generated_inside_the_task(self, parent):
        """It is a live credential, so it must not travel as a task argument —
        those are serialised into the broker and printed by Celery's logging.
        The signature is (parent_id, login_url, reset) and nothing else."""
        with patch("comms.services.email_service.EmailService.send_email", return_value=True):
            result = send_parent_temporary_password_task.run(parent.id, LOGIN_URL)

        # ...and it must not come back out in the return value either, which the
        # result backend stores.
        assert "temporary_password" not in result
        assert set(result) == {"status", "recipient"}

    def test_an_existing_password_keeps_working(self, parent):
        """Recovery is unauthenticated, so issuing a temporary password must not
        overwrite the credential the family may still be using — otherwise
        anyone who knows an address can lock them out."""
        parent.set_portal_password("Portal-Fam-2026")

        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
            send_parent_temporary_password_task.run(parent.id, LOGIN_URL, True)

        emailed = mock_send.call_args[1]["context"]["temporary_password"]
        parent.refresh_from_db()
        assert parent.authenticate_portal("Portal-Fam-2026") == "password"
        assert parent.authenticate_portal(emailed) == "temporary"

    def test_the_reset_flavour_changes_the_subject(self, parent):
        """One template, two callers — the invitation and the recovery form
        differ in copy, not in mechanism."""
        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
            send_parent_temporary_password_task.run(parent.id, LOGIN_URL, True)
        _, kwargs = mock_send.call_args
        assert kwargs["context"]["reset"] is True
        assert "temporal" in kwargs["subject"].lower()

    def test_failure_reported(self, parent):
        with patch("comms.services.email_service.EmailService.send_email", return_value=False):
            result = send_parent_temporary_password_task.run(parent.id, LOGIN_URL)
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
