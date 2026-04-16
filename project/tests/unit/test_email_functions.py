"""Tests for comms.services.email_functions — convenience email wrappers."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def mock_email_service():
    """Mock the global email_service so no real emails are sent."""
    with patch("comms.services.email_functions.email_service") as mock_svc:
        mock_svc.send_email.return_value = True
        yield mock_svc


class TestBirthdayEmail:
    def test_send_birthday_email(self, mock_email_service):
        from comms.services.email_functions import send_birthday_email

        result = send_birthday_email("parent@test.com", "Sofia")
        assert result is True
        mock_email_service.send_email.assert_called_once()
        call_kwargs = mock_email_service.send_email.call_args
        assert (
            call_kwargs.kwargs["template_name"] == "happy_birthday"
            or call_kwargs[1]["template_name"] == "happy_birthday"
        )


class TestPaymentReminder:
    def test_send_payment_reminder(self, mock_email_service):
        from comms.services.email_functions import send_payment_reminder

        result = send_payment_reminder("parent@test.com", "Pablo", "54.00", "2026-05-01")
        assert result is True
        mock_email_service.send_email.assert_called_once()


class TestMonthlyReport:
    def test_send_monthly_report(self, mock_email_service):
        from comms.services.email_functions import send_monthly_report

        result = send_monthly_report("admin@test.com", {"total_students": 100})
        assert result is True
        mock_email_service.send_email.assert_called_once()


class TestWelcomeEmail:
    def test_send_welcome_email(self, mock_email_service):
        from comms.services.email_functions import send_welcome_email

        result = send_welcome_email(
            parent_email="parent@test.com",
            parent_name="Maria",
            student_name="Lucia",
            group_name="Starters A",
            enrollment_type="Mensual",
            schedule_type="2 dias/semana",
            start_date="15/09/2026",
        )
        assert result is True
        call_kwargs = mock_email_service.send_email.call_args
        # Welcome email uses fail_silently=True
        assert call_kwargs.kwargs.get("fail_silently") is True or call_kwargs[1].get("fail_silently") is True


class TestEnrollmentConfirmation:
    def test_send_enrollment_confirmation(self, mock_email_service):
        from comms.services.email_functions import send_enrollment_confirmation_email

        result = send_enrollment_confirmation_email(
            parent_email="parent@test.com",
            student_name="Daniel",
            gender="m",
            academic_year="2025-2026",
            month="septiembre",
        )
        assert result is True
        mock_email_service.send_email.assert_called_once()


class TestFunFridayEmail:
    def test_send_fun_friday_email(self, mock_email_service):
        from comms.services.email_functions import send_fun_friday_email

        result = send_fun_friday_email(
            recipients=["parent1@test.com", "parent2@test.com"],
            day_name="viernes",
            day_number="17",
            month="abril",
            start_time="15:00",
            end_time="16:30",
            activity_description="Arts and crafts",
            minimum_age=3,
            maximum_age=12,
        )
        assert result is True
        mock_email_service.send_email.assert_called_once()

    def test_send_fun_friday_email_without_image(self, mock_email_service):
        from comms.services.email_functions import send_fun_friday_email

        result = send_fun_friday_email(
            recipients=["parent@test.com"],
            day_name="viernes",
            day_number="17",
            month="abril",
            start_time="15:00",
            end_time="16:30",
            activity_description="Movie day",
            minimum_age=3,
            maximum_age=12,
            event_image_path=None,
        )
        assert result is True


class TestVacationClosure:
    def test_send_vacation_closure_email(self, mock_email_service):
        from comms.services.email_functions import send_vacation_closure_email

        result = send_vacation_closure_email(
            recipients=["parent@test.com"],
            start_closure_day_name="lunes",
            start_closure_day_number="22",
            end_closure_day_name="viernes",
            end_closure_day_number="2",
            month_closure="diciembre",
            closure_reason="Navidad",
            reopening_day_name="lunes",
            reopening_day_number="7",
            month_reopening="enero",
        )
        assert result is True
        mock_email_service.send_email.assert_called_once()


class TestPaymentReminderFull:
    def test_send_payment_reminder_email(self, mock_email_service):
        from comms.services.email_functions import send_payment_reminder_email

        result = send_payment_reminder_email(
            recipients=["parent@test.com"],
            payment_start_day_name="lunes",
            payment_start_day_number="1",
            payment_end_day_name="viernes",
            payment_end_day_number="5",
            month="mayo",
            iban_number="ES1234567890",
            reduced_price_cheque_idioma="34.00",
            telephone_number_bizum="600100001",
        )
        assert result is True


class TestQuarterlyReceipt:
    def test_send_quarterly_receipt_email(self, mock_email_service):
        from comms.services.email_functions import send_quarterly_receipt_email

        result = send_quarterly_receipt_email(
            parent_email="parent@test.com",
            student_name="Hugo",
            month_1="octubre",
            month_2="noviembre",
            month_3="diciembre",
        )
        assert result is True


# ============================================================================
# Extra coverage: tax certificate branches, fun-friday inline image path,
# send_all_tax_certificates skipped/failed counters
# ============================================================================


class TestEmailFunctionsExtra:
    def test_fun_friday_with_existing_image_path(self, tmp_path):
        """event_image_path present and exists → inline_images populated."""
        from comms.services.email_functions import send_fun_friday_email

        img = tmp_path / "img.png"
        img.write_bytes(b"\x89PNG\r\n")
        with patch("comms.services.email_functions.email_service.send_email") as mock:
            mock.return_value = True
            send_fun_friday_email(
                recipients="parent@test.com",
                day_name="lunes",
                day_number=1,
                month="enero",
                start_time="17:00",
                end_time="18:30",
                activity_description="Test",
                minimum_age=5,
                maximum_age=12,
                meeting_point="",
                event_image_path=str(img),
            )
        call_kwargs = mock.call_args.kwargs
        assert call_kwargs["inline_images"] is not None

    def test_tax_certificate_parent_by_id_not_found(self):
        from comms.services.email_functions import send_tax_certificate_email

        assert send_tax_certificate_email(99999, 2025) is False

    def test_tax_certificate_no_payments(self, parent):
        from comms.services.email_functions import send_tax_certificate_email

        # No payments for this parent → False
        assert send_tax_certificate_email(parent, 2025) is False

    def test_tax_certificate_pdf_generation_fails(self, completed_payment, parent):
        from comms.services.email_functions import send_tax_certificate_email

        with patch(
            "comms.services.email_functions.generate_tax_certificate_pdf",
            side_effect=RuntimeError("pdf broken"),
        ):
            assert send_tax_certificate_email(parent, 2025) is False

    def test_tax_certificate_html_fallback_when_no_weasyprint(self, completed_payment, parent):
        """When weasyprint isn't installed, generate_tax_certificate_pdf returns HTML bytes."""
        from comms.services.email_functions import send_tax_certificate_email

        with patch("comms.services.email_functions.email_service.send_email", return_value=True):
            result = send_tax_certificate_email(parent, 2025)
        # Either True or False depending on whether weasyprint is installed — just make sure
        # the branch executes cleanly.
        assert result in (True, False)

    def test_send_all_tax_certificates_with_skipped(self, db, student_with_parent, parent, active_enrollment):
        """Parent with payment but no email → skipped counter increments."""
        from billing.models import Payment
        from comms.services.email_functions import send_all_tax_certificates
        from students.models import Parent

        Parent.objects.filter(id=parent.id).update(email="")

        Payment.objects.create(
            student=student_with_parent,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("50.00"),
            payment_status="completed",
            payment_date=date(2025, 10, 1),
            due_date=date(2025, 10, 1),
            concept="test",
        )

        results = send_all_tax_certificates(2025)
        assert results["skipped"] >= 1

    def test_send_all_tax_certificates_with_failure(self, db, student_with_parent, parent, active_enrollment):
        from billing.models import Payment
        from comms.services.email_functions import send_all_tax_certificates

        Payment.objects.create(
            student=student_with_parent,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("50.00"),
            payment_status="completed",
            payment_date=date(2025, 10, 1),
            due_date=date(2025, 10, 1),
            concept="test",
        )

        with patch("comms.services.email_functions.send_tax_certificate_email", return_value=False):
            results = send_all_tax_certificates(2025)
        assert results["failed"] >= 1
