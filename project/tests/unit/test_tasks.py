"""Tests for comms.tasks — Celery tasks for async email sending.

Tasks are called synchronously (CELERY_TASK_ALWAYS_EAGER=True in test settings,
or by invoking the underlying function directly). Email sending is mocked so
no real SMTP calls happen.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from billing.models import Enrollment, Payment
from comms.tasks import (
    send_birthday_email_task,
    send_birthday_emails_task,
    send_enrollment_confirmation_task,
    send_generic_email_task,
    send_payment_reminders,
    send_welcome_email_task,
)
from students.models import Parent, Student, StudentParent

pytestmark = pytest.mark.django_db


# ============================================================================
# send_welcome_email_task
# ============================================================================


class TestSendWelcomeEmailTask:
    def test_success_with_parent(self, student_with_parent, active_enrollment, parent):
        with patch("comms.services.email_service.email_service.send_email") as mock_send:
            mock_send.return_value = True
            result = send_welcome_email_task(
                parent_id=parent.id,
                student_id=student_with_parent.id,
                enrollment_id=active_enrollment.id,
            )
        assert result["status"] == "success"
        assert result["recipient"] == parent.email
        mock_send.assert_called_once()

    def test_success_with_adult_student(self, adult_student, enrollment_type_adults, site_config):
        enr = Enrollment.objects.create(
            student=adult_student,
            enrollment_type=enrollment_type_adults,
            enrollment_period_start=date(2025, 9, 15),
            enrollment_period_end=date(2026, 6, 27),
            academic_year="2025-2026",
            schedule_type="full_time",
            payment_modality="monthly",
            enrollment_amount=Decimal("60.00"),
            final_amount=Decimal("60.00"),
            status="active",
            enrollment_date=date(2025, 9, 1),
        )
        with patch("comms.services.email_service.email_service.send_email") as mock_send:
            mock_send.return_value = True
            result = send_welcome_email_task(
                parent_id=None,
                student_id=adult_student.id,
                enrollment_id=enr.id,
            )
        assert result["status"] == "success"
        assert result["recipient"] == adult_student.email

    def test_special_enrollment_reports_special_payment_modality(
        self, student_with_parent, parent, enrollment_type_special, site_config
    ):
        """A hand-priced matrícula has no standard cadence to announce."""

        enr = Enrollment.objects.create(
            student=student_with_parent,
            enrollment_type=enrollment_type_special,
            enrollment_period_start=date(2025, 9, 15),
            enrollment_period_end=date(2026, 6, 27),
            academic_year="2025-2026",
            schedule_type="full_time",
            payment_modality="monthly",
            enrollment_amount=Decimal("55.00"),
            final_amount=Decimal("55.00"),
            status="active",
            enrollment_date=date(2025, 9, 1),
        )
        with patch("comms.services.email_service.email_service.send_email") as mock_send:
            mock_send.return_value = True
            send_welcome_email_task(
                parent_id=parent.id,
                student_id=student_with_parent.id,
                enrollment_id=enr.id,
            )
        context = mock_send.call_args.kwargs["context"]
        assert context["enrollment_type"] == "Especial"
        assert context["payment_modality"] == "Especial"

    def test_standard_enrollment_keeps_its_cadence(self, student_with_parent, parent, active_enrollment):
        with patch("comms.services.email_service.email_service.send_email") as mock_send:
            mock_send.return_value = True
            send_welcome_email_task(
                parent_id=parent.id,
                student_id=student_with_parent.id,
                enrollment_id=active_enrollment.id,
            )
        assert mock_send.call_args.kwargs["context"]["payment_modality"] == "Mensual"

    def test_no_email_address_returns_skipped(self, student, active_enrollment):
        # Student has no email, parent_id=None → recipient_email empty
        result = send_welcome_email_task(
            parent_id=None,
            student_id=student.id,
            enrollment_id=active_enrollment.id,
        )
        assert result["status"] == "skipped"

    def test_missing_student_returns_error(self, parent, active_enrollment):
        result = send_welcome_email_task(
            parent_id=parent.id,
            student_id=99999,
            enrollment_id=active_enrollment.id,
        )
        assert result["status"] == "error"

    def test_missing_enrollment_returns_error(self, student, parent):
        result = send_welcome_email_task(
            parent_id=parent.id,
            student_id=student.id,
            enrollment_id=99999,
        )
        assert result["status"] == "error"

    def test_send_failure_raises_for_retry(self, student_with_parent, active_enrollment, parent):
        with patch("comms.services.email_service.email_service.send_email") as mock_send:
            mock_send.return_value = False
            with pytest.raises(RuntimeError):
                send_welcome_email_task(
                    parent_id=parent.id,
                    student_id=student_with_parent.id,
                    enrollment_id=active_enrollment.id,
                )


# ============================================================================
# send_birthday_email_task
# ============================================================================


class TestSendBirthdayEmailTask:
    def test_success(self, student_with_parent):
        with patch("comms.services.email_service.email_service.send_email") as mock_send:
            mock_send.return_value = True
            result = send_birthday_email_task(student_id=student_with_parent.id)
        assert result["status"] == "success"

    def test_student_without_parent_email_skipped(self, student):
        # student has no parent
        result = send_birthday_email_task(student_id=student.id)
        assert result["status"] == "skipped"

    def test_missing_student_returns_error(self):
        result = send_birthday_email_task(student_id=99999)
        assert result["status"] == "error"

    def test_send_failure_raises(self, student_with_parent):
        with patch("comms.services.email_service.email_service.send_email") as mock_send:
            mock_send.return_value = False
            with pytest.raises(RuntimeError):
                send_birthday_email_task(student_id=student_with_parent.id)


# ============================================================================
# send_birthday_emails_task (scheduled wrapper)
# ============================================================================


class TestSendBirthdayEmailsTask:
    def test_no_birthdays_today(self, db):
        result = send_birthday_emails_task()
        assert result["status"] == "success"
        assert result["birthdays_found"] == 0

    def test_finds_and_queues_birthday(self, db, group):
        today = date.today()
        # Student whose birth_date month/day matches today
        Student.objects.create(
            first_name="Birthday",
            last_name="Kid",
            birth_date=date(2018, today.month, today.day),
            gdpr_signed=True,
            group=group,
            active=True,
        )

        with patch("comms.tasks.send_birthday_email_task.delay") as mock_delay:
            result = send_birthday_emails_task()
        assert result["status"] == "success"
        assert result["birthdays_found"] >= 1
        assert result["tasks_queued"] >= 1
        mock_delay.assert_called()


# ============================================================================
# send_payment_reminders (weekly task)
# ============================================================================


class TestSendPaymentReminders:
    def test_no_pending_payments(self, db):
        result = send_payment_reminders()
        assert result["status"] == "no_pending_payments"
        assert result["sent"] == 0

    def test_sends_bulk_reminders(self, db, student_with_parent, parent, active_enrollment):
        # Payment due in 3 days
        Payment.objects.create(
            student=student_with_parent,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("54.00"),
            payment_status="pending",
            due_date=date.today() + timedelta(days=3),
            concept="Test pending",
        )

        with patch("comms.services.email_service.email_service.send_bulk_emails") as mock_bulk:
            mock_bulk.return_value = {"sent": 1, "failed": 0}
            result = send_payment_reminders()
        assert result["sent"] == 1
        mock_bulk.assert_called_once()

    def test_skips_parents_without_email(self, db, student, active_enrollment):
        """Payment whose parent has no email is filtered out."""

        p = Parent.objects.create(
            first_name="NoMail",
            last_name="Parent",
            dni="99999999X",
            phone="600000000",
            email="",
        )
        StudentParent.objects.create(student=student, parent=p)
        Payment.objects.create(
            student=student,
            parent=p,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("54.00"),
            payment_status="pending",
            due_date=date.today() + timedelta(days=3),
            concept="No mail",
        )

        with patch("comms.services.email_service.email_service.send_bulk_emails") as mock_bulk:
            mock_bulk.return_value = {"sent": 0, "failed": 0}
            send_payment_reminders()
        # Called with empty emails_data
        assert mock_bulk.call_args.kwargs["emails_data"] == []


# ============================================================================
# send_generic_email_task
# ============================================================================


class TestSendGenericEmailTask:
    def test_success(self):
        with patch("comms.services.email_service.email_service.send_email") as mock_send:
            mock_send.return_value = True
            result = send_generic_email_task(
                template_name="test_template",
                recipient_email="test@example.com",
                subject="Test",
                context={"foo": "bar"},
            )
        assert result["status"] == "success"

    def test_failure_raises(self):
        with patch("comms.services.email_service.email_service.send_email") as mock_send:
            mock_send.return_value = False
            with pytest.raises(RuntimeError):
                send_generic_email_task(
                    template_name="broken",
                    recipient_email="test@example.com",
                    subject="Fail",
                )

    def test_with_none_context_defaults_to_empty(self):
        with patch("comms.services.email_service.email_service.send_email") as mock_send:
            mock_send.return_value = True
            send_generic_email_task(
                template_name="t",
                recipient_email="t@t.com",
                subject="s",
                context=None,
            )
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["context"] == {}


# ============================================================================
# send_enrollment_confirmation_task
# ============================================================================


class TestSendEnrollmentConfirmationTask:
    def test_success(self, student_with_parent, active_enrollment, parent):
        with patch("comms.tasks.send_enrollment_confirmation_email") as mock_send:
            mock_send.return_value = True
            result = send_enrollment_confirmation_task(enrollment_id=active_enrollment.id)
        assert result["status"] == "success"
        assert result["recipient"] == parent.email
        mock_send.assert_called_once()

    def test_missing_enrollment_returns_error(self):
        result = send_enrollment_confirmation_task(enrollment_id=99999)
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    def test_no_parent_with_email_returns_error(self, student, active_enrollment):
        """Student with no parents → task returns error."""

        # `student` fixture has no parents attached — student_with_parent does.
        result = send_enrollment_confirmation_task(enrollment_id=active_enrollment.id)
        assert result["status"] == "error"

    def test_send_failure_raises(self, student_with_parent, active_enrollment):
        with patch("comms.tasks.send_enrollment_confirmation_email") as mock_send:
            mock_send.return_value = False
            with pytest.raises(RuntimeError):
                send_enrollment_confirmation_task(enrollment_id=active_enrollment.id)

    def test_with_attachments(self, student_with_parent, active_enrollment, tmp_path):
        # Create a fake PDF file on disk
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 dummy")

        with patch("comms.tasks.send_enrollment_confirmation_email") as mock_send:
            mock_send.return_value = True
            result = send_enrollment_confirmation_task(
                enrollment_id=active_enrollment.id,
                attachments_paths=[str(pdf)],
            )
        assert result["status"] == "success"
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["attachments"] is not None
        assert len(call_kwargs["attachments"]) == 1

    def test_nonexistent_attachment_path_skipped(self, student_with_parent, active_enrollment):
        with patch("comms.tasks.send_enrollment_confirmation_email") as mock_send:
            mock_send.return_value = True
            send_enrollment_confirmation_task(
                enrollment_id=active_enrollment.id,
                attachments_paths=["/nonexistent/path.pdf"],
            )
        call_kwargs = mock_send.call_args.kwargs
        # Missing file path is silently skipped; attachments list is empty → None
        assert call_kwargs["attachments"] is None
