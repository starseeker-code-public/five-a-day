"""Tests for core.views.app_forms — email form GET pages and POST preview/send."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestFunFridayForm:
    def test_get_renders_form(self, authenticated_client):
        response = authenticated_client.get(reverse("fun_friday_form"))
        assert response.status_code == 200
        assert "next_friday" in response.context
        assert "email_html" in response.context

    def test_preview_returns_html(self, authenticated_client):
        next_friday = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 or 7)
        response = authenticated_client.post(
            reverse("fun_friday_form"),
            {
                "action": "preview",
                "event_date": next_friday.isoformat(),
                "start_time": "17:00",
                "end_time": "18:30",
                "activity_description": "<b>Test</b>",
                "min_age": "5",
                "max_age": "12",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "html" in data

    def test_send_all_missing_fields_shows_error(self, authenticated_client):
        response = authenticated_client.post(
            reverse("fun_friday_form"),
            {"event_date": "", "start_time": "", "end_time": ""},
        )
        assert response.status_code == 200  # re-renders form

    def test_send_all_to_parents(self, authenticated_client, student_with_parent):
        next_friday = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 or 7)
        response = authenticated_client.post(
            reverse("fun_friday_form"),
            {
                "event_date": next_friday.isoformat(),
                "start_time": "17:00",
                "end_time": "18:30",
                "activity_description": "<b>Crafts</b>",
                "min_age": "5",
                "max_age": "12",
                "meeting_point": "Main entrance",
            },
        )
        assert response.status_code == 302  # redirects to home


class TestPaymentReminderForm:
    def test_get_renders_form(self, authenticated_client):
        response = authenticated_client.get(reverse("payment_reminder_form"))
        assert response.status_code == 200
        assert "email_html" in response.context

    def test_preview_returns_html(self, authenticated_client):
        response = authenticated_client.post(
            reverse("payment_reminder_form"),
            {
                "action": "preview",
                "payment_start_date": "2026-04-01",
                "payment_end_date": "2026-04-05",
                "month": "abril",
                "iban_number": "ES1234",
                "telephone_number_bizum": "600000000",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "html" in data

    def test_send_missing_fields_shows_error(self, authenticated_client):
        response = authenticated_client.post(
            reverse("payment_reminder_form"),
            {"payment_start_date": "2026-04-01"},
        )
        assert response.status_code == 200

    def test_send_to_parents(self, authenticated_client, student_with_parent):
        response = authenticated_client.post(
            reverse("payment_reminder_form"),
            {
                "payment_start_date": "2026-04-01",
                "payment_end_date": "2026-04-05",
                "month": "abril",
                "iban_number": "ES1234567890",
                "telephone_number_bizum": "600000000",
                "reduced_price_cheque_idioma": "34€",
            },
        )
        assert response.status_code == 302


class TestVacationClosureForm:
    def test_get_renders_form(self, authenticated_client):
        response = authenticated_client.get(reverse("vacation_closure_form"))
        assert response.status_code == 200
        assert "email_html" in response.context

    def test_preview_returns_html(self, authenticated_client):
        response = authenticated_client.post(
            reverse("vacation_closure_form"),
            {
                "action": "preview",
                "closure_start_date": "2026-12-23",
                "closure_end_date": "2027-01-03",
                "reopening_date": "2027-01-08",
                "closure_reason": "Navidad",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "html" in data

    def test_send_missing_fields_shows_error(self, authenticated_client):
        response = authenticated_client.post(
            reverse("vacation_closure_form"),
            {"closure_start_date": "2026-12-23"},
        )
        assert response.status_code == 200

    def test_send_to_parents(self, authenticated_client, student_with_parent):
        response = authenticated_client.post(
            reverse("vacation_closure_form"),
            {
                "closure_start_date": "2026-12-23",
                "closure_end_date": "2027-01-03",
                "reopening_date": "2027-01-08",
                "closure_reason": "Navidad",
            },
        )
        assert response.status_code == 302


class TestTaxCertificateForm:
    def test_get_renders_form(self, authenticated_client):
        response = authenticated_client.get(reverse("tax_certificate_form"))
        assert response.status_code == 200

    def test_preview_returns_html(self, authenticated_client):
        response = authenticated_client.post(
            reverse("tax_certificate_form"),
            {"action": "preview", "year": "2025"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "html" in data

    def test_send_certificates(self, authenticated_client, completed_payment):
        response = authenticated_client.post(
            reverse("tax_certificate_form"),
            {"year": "2025"},
        )
        assert response.status_code == 302


class TestMonthlyReportForm:
    def test_get_renders_form(self, authenticated_client):
        response = authenticated_client.get(reverse("monthly_report_form"))
        assert response.status_code == 200

    def test_preview_returns_html(self, authenticated_client):
        response = authenticated_client.post(
            reverse("monthly_report_form"),
            {"action": "preview"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "html" in data


class TestReceiptsForm:
    def test_get_renders_form(self, authenticated_client):
        response = authenticated_client.get(reverse("receipts_form"))
        assert response.status_code == 200


class TestBirthdayForm:
    def test_get_renders_form(self, authenticated_client):
        response = authenticated_client.get(reverse("birthday_form"))
        assert response.status_code == 200


class TestEnrollmentForm:
    def test_get_renders_form(self, authenticated_client):
        response = authenticated_client.get(reverse("enrollment_form"))
        assert response.status_code == 200


class TestTestSendNoEnvVars:
    """Test that test_send fails gracefully without EMAIL_TEST_* env vars."""

    def test_fun_friday_test_send_no_env(self, authenticated_client, monkeypatch):
        monkeypatch.delenv("EMAIL_TEST_1", raising=False)
        monkeypatch.delenv("EMAIL_TEST_2", raising=False)
        next_friday = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 or 7)
        response = authenticated_client.post(
            reverse("fun_friday_form"),
            {
                "action": "test_send",
                "event_date": next_friday.isoformat(),
                "start_time": "17:00",
                "end_time": "18:30",
                "activity_description": "Test",
                "min_age": "5",
                "max_age": "12",
            },
        )
        data = response.json()
        assert data["success"] is False

    def test_payment_reminder_test_send_no_env(self, authenticated_client, monkeypatch):
        monkeypatch.delenv("EMAIL_TEST_1", raising=False)
        monkeypatch.delenv("EMAIL_TEST_2", raising=False)
        response = authenticated_client.post(
            reverse("payment_reminder_form"),
            {
                "action": "test_send",
                "payment_start_date": "2026-04-01",
                "payment_end_date": "2026-04-05",
                "month": "abril",
                "iban_number": "ES1234",
                "telephone_number_bizum": "600000000",
            },
        )
        data = response.json()
        assert data["success"] is False

    def test_vacation_closure_test_send_no_env(self, authenticated_client, monkeypatch):
        monkeypatch.delenv("EMAIL_TEST_1", raising=False)
        monkeypatch.delenv("EMAIL_TEST_2", raising=False)
        response = authenticated_client.post(
            reverse("vacation_closure_form"),
            {
                "action": "test_send",
                "closure_start_date": "2026-12-23",
                "closure_end_date": "2027-01-03",
                "reopening_date": "2027-01-08",
                "closure_reason": "Navidad",
            },
        )
        data = response.json()
        assert data["success"] is False

    def test_tax_certificate_test_send_no_env(self, authenticated_client, monkeypatch):
        monkeypatch.delenv("EMAIL_TEST_1", raising=False)
        monkeypatch.delenv("EMAIL_TEST_2", raising=False)
        response = authenticated_client.post(
            reverse("tax_certificate_form"),
            {"action": "test_send", "year": "2025"},
        )
        data = response.json()
        assert data["success"] is False


# ============================================================================
# Extra coverage: main send-to-parents paths, test_send with env vars SET,
# invalid-date fallbacks, no-parents-with-email edge cases, exception paths
# ============================================================================

pytestmark = pytest.mark.django_db


@pytest.fixture
def email_test_env(monkeypatch):
    """Simulate EMAIL_TEST_1/2 env vars being configured."""
    monkeypatch.setenv("EMAIL_TEST_1", "qa1@test.local")
    monkeypatch.setenv("EMAIL_TEST_2", "qa2@test.local")


@pytest.fixture
def mock_email_svc():
    """Patch the global email_service so send_email always succeeds."""
    with patch("core.views.app_forms.email_service") as svc:
        svc.send_email.return_value = True
        yield svc


# ============================================================================
# Fun Friday — test_send with env, date parse errors, send_all paths
# ============================================================================


class TestFunFridayExtra:
    def test_test_send_with_env_sends_email(self, authenticated_client, email_test_env, mock_email_svc):
        next_friday = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 or 7)
        response = authenticated_client.post(
            reverse("fun_friday_form"),
            {
                "action": "test_send",
                "event_date": next_friday.isoformat(),
                "start_time": "17:00",
                "end_time": "18:30",
                "activity_description": "Test",
                "min_age": "5",
                "max_age": "12",
                "meeting_point": "Main entrance",
            },
        )
        data = response.json()
        assert data["success"] is True
        mock_email_svc.send_email.assert_called_once()

    def test_test_send_failure_returns_error(self, authenticated_client, email_test_env):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = False
            next_friday = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 or 7)
            response = authenticated_client.post(
                reverse("fun_friday_form"),
                {
                    "action": "test_send",
                    "event_date": next_friday.isoformat(),
                    "start_time": "17:00",
                    "end_time": "18:30",
                    "activity_description": "Test",
                    "min_age": "5",
                    "max_age": "12",
                },
            )
        assert response.json()["success"] is False

    def test_invalid_date_uses_default(self, authenticated_client, email_test_env, mock_email_svc):
        """Invalid event_date format → default next_friday used, no crash."""
        response = authenticated_client.post(
            reverse("fun_friday_form"),
            {
                "action": "preview",
                "event_date": "not-a-date",
                "start_time": "17:00",
                "end_time": "18:30",
                "activity_description": "Test",
                "min_age": "5",
                "max_age": "12",
            },
        )
        assert response.status_code == 200
        assert "html" in response.json()

    def test_invalid_age_uses_default(self, authenticated_client, email_test_env, mock_email_svc):
        """Invalid min_age/max_age → defaults to 5/12."""
        response = authenticated_client.post(
            reverse("fun_friday_form"),
            {
                "action": "preview",
                "event_date": "2026-05-01",
                "start_time": "17:00",
                "end_time": "18:30",
                "activity_description": "Test",
                "min_age": "not-a-number",
                "max_age": "also-not-a-number",
            },
        )
        assert response.status_code == 200

    def test_send_all_with_email_failures(self, authenticated_client, student_with_parent):
        """Exceptions during send don't break the form."""
        with patch("core.views.app_forms.send_fun_friday_email", side_effect=Exception("SMTP down")):
            next_friday = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 or 7)
            response = authenticated_client.post(
                reverse("fun_friday_form"),
                {
                    "event_date": next_friday.isoformat(),
                    "start_time": "17:00",
                    "end_time": "18:30",
                    "activity_description": "<b>Crafts</b>",
                    "min_age": "5",
                    "max_age": "12",
                    "meeting_point": "Main entrance",
                },
            )
        assert response.status_code == 302


# ============================================================================
# Payment reminder — test_send with env, main send path
# ============================================================================


class TestPaymentReminderExtra:
    def test_test_send_with_env_sends(self, authenticated_client, email_test_env, mock_email_svc):
        response = authenticated_client.post(
            reverse("payment_reminder_form"),
            {
                "action": "test_send",
                "payment_start_date": "2026-04-01",
                "payment_end_date": "2026-04-05",
                "month": "abril",
                "iban_number": "ES1234",
                "telephone_number_bizum": "600000000",
                "iban_holder": "Test Holder",
                "full_time_fee": "54",
                "part_time_fee": "36",
                "adult_fee": "60",
                "reduced_price_cheque_idioma": "34€",
            },
        )
        assert response.json()["success"] is True

    def test_test_send_fail_response(self, authenticated_client, email_test_env):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = False
            response = authenticated_client.post(
                reverse("payment_reminder_form"),
                {
                    "action": "test_send",
                    "payment_start_date": "2026-04-01",
                    "payment_end_date": "2026-04-05",
                    "month": "abril",
                    "iban_number": "ES1234",
                    "telephone_number_bizum": "600000000",
                },
            )
        assert response.json()["success"] is False

    def test_send_all_with_failures(self, authenticated_client, student_with_parent):
        with patch("core.views.app_forms.send_payment_reminder_email", side_effect=Exception("smtp")):
            response = authenticated_client.post(
                reverse("payment_reminder_form"),
                {
                    "payment_start_date": "2026-04-01",
                    "payment_end_date": "2026-04-05",
                    "month": "abril",
                    "iban_number": "ES1234567890",
                    "telephone_number_bizum": "600000000",
                    "reduced_price_cheque_idioma": "34€",
                },
            )
        assert response.status_code == 302


# ============================================================================
# Vacation closure — test_send, invalid dates, no parents
# ============================================================================


class TestVacationClosureExtra:
    def test_test_send_with_env(self, authenticated_client, email_test_env, mock_email_svc):
        response = authenticated_client.post(
            reverse("vacation_closure_form"),
            {
                "action": "test_send",
                "closure_start_date": "2026-12-23",
                "closure_end_date": "2027-01-03",
                "reopening_date": "2027-01-08",
                "closure_reason": "Navidad",
            },
        )
        assert response.json()["success"] is True

    def test_test_send_fail(self, authenticated_client, email_test_env):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = False
            response = authenticated_client.post(
                reverse("vacation_closure_form"),
                {
                    "action": "test_send",
                    "closure_start_date": "2026-12-23",
                    "closure_end_date": "2027-01-03",
                    "reopening_date": "2027-01-08",
                    "closure_reason": "Navidad",
                },
            )
        assert response.json()["success"] is False

    def test_test_send_invalid_dates_fallback(self, authenticated_client, email_test_env, mock_email_svc):
        response = authenticated_client.post(
            reverse("vacation_closure_form"),
            {
                "action": "preview",
                "closure_start_date": "not-a-date",
                "closure_end_date": "also-not",
                "reopening_date": "nope",
                "closure_reason": "Emergency",
            },
        )
        assert response.status_code == 200

    def test_invalid_date_on_main_send(self, authenticated_client, student_with_parent):
        response = authenticated_client.post(
            reverse("vacation_closure_form"),
            {
                "closure_start_date": "not-a-date",
                "closure_end_date": "2027-01-03",
                "reopening_date": "2027-01-08",
                "closure_reason": "Navidad",
            },
        )
        assert response.status_code == 302

    def test_no_parents_with_email(self, authenticated_client, db, group):
        """If no parents have email, warning + redirect to apps."""
        from students.models import Parent, Student, StudentParent

        no_email_parent = Parent.objects.create(
            first_name="X",
            last_name="Y",
            dni="11111111A",
            phone="600111111",
            email="",
        )
        s = Student.objects.create(
            first_name="Kid",
            last_name="NoMail",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
            active=True,
        )
        StudentParent.objects.create(student=s, parent=no_email_parent)

        response = authenticated_client.post(
            reverse("vacation_closure_form"),
            {
                "closure_start_date": "2026-12-23",
                "closure_end_date": "2027-01-03",
                "reopening_date": "2027-01-08",
                "closure_reason": "Navidad",
            },
        )
        assert response.status_code == 302


# ============================================================================
# Tax certificate — test_send + main send
# ============================================================================


class TestTaxCertificateExtra:
    def test_test_send_with_env(self, authenticated_client, email_test_env, mock_email_svc):
        response = authenticated_client.post(
            reverse("tax_certificate_form"),
            {"action": "test_send", "year": "2025"},
        )
        assert response.json()["success"] is True

    def test_test_send_fail(self, authenticated_client, email_test_env):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = False
            response = authenticated_client.post(
                reverse("tax_certificate_form"),
                {"action": "test_send", "year": "2025"},
            )
        assert response.json()["success"] is False

    def test_main_send_with_skipped_and_failed(self, authenticated_client, completed_payment):
        with patch("core.views.app_forms.send_all_tax_certificates") as mock_send:
            mock_send.return_value = {"sent": 1, "skipped": 2, "failed": 1}
            response = authenticated_client.post(
                reverse("tax_certificate_form"),
                {"year": "2025"},
            )
        assert response.status_code == 302


# ============================================================================
# Monthly report — test_send, main send, errors
# ============================================================================


class TestMonthlyReportExtra:
    def test_test_send_with_env(self, authenticated_client, email_test_env, mock_email_svc):
        response = authenticated_client.post(
            reverse("monthly_report_form"),
            {
                "action": "test_send",
                "month": "abril",
                "year": "2026",
            },
        )
        assert response.json()["success"] is True

    def test_test_send_invalid_year_falls_back(self, authenticated_client, email_test_env, mock_email_svc):
        response = authenticated_client.post(
            reverse("monthly_report_form"),
            {
                "action": "preview",
                "month": "abril",
                "year": "not-a-year",
            },
        )
        assert response.status_code == 200

    def test_test_send_fail(self, authenticated_client, email_test_env):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = False
            response = authenticated_client.post(
                reverse("monthly_report_form"),
                {"action": "test_send", "month": "abril", "year": "2026"},
            )
        assert response.json()["success"] is False

    def test_main_send_to_parents(self, authenticated_client, student_with_parent):
        with patch("core.views.app_forms.send_monthly_report") as mock_send:
            mock_send.return_value = True
            response = authenticated_client.post(
                reverse("monthly_report_form"),
                {"month": "abril", "year": "2026"},
            )
        assert response.status_code == 302
        mock_send.assert_called()

    def test_main_send_with_failures(self, authenticated_client, student_with_parent):
        with patch("core.views.app_forms.send_monthly_report", side_effect=Exception("boom")):
            response = authenticated_client.post(
                reverse("monthly_report_form"),
                {"month": "abril", "year": "2026"},
            )
        assert response.status_code == 302


# ============================================================================
# Birthday form — test_send, main send with and without birthdays
# ============================================================================


class TestBirthdayFormExtra:
    def test_test_send_with_env_no_birthdays(self, authenticated_client, email_test_env, mock_email_svc):
        """No birthdays today → uses 'Alumno Ejemplo' placeholder."""
        response = authenticated_client.post(
            reverse("birthday_form"),
            {"action": "test_send"},
        )
        assert response.json()["success"] is True

    def test_test_send_with_env_fail(self, authenticated_client, email_test_env):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = False
            response = authenticated_client.post(
                reverse("birthday_form"),
                {"action": "test_send"},
            )
        assert response.json()["success"] is False

    def test_main_send_no_birthdays(self, authenticated_client):
        """No birthdays today → info message + redirect to birthday_form."""
        response = authenticated_client.post(reverse("birthday_form"), {})
        assert response.status_code == 302

    def test_main_send_with_birthday_today(self, authenticated_client, db, group, parent):
        """A student with a birthday today triggers an email send."""
        from students.models import Student, StudentParent

        today = date.today()
        s = Student.objects.create(
            first_name="Birthday",
            last_name="Kid",
            birth_date=date(2018, today.month, today.day),
            gdpr_signed=True,
            group=group,
            active=True,
        )
        StudentParent.objects.create(student=s, parent=parent)

        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = True
            response = authenticated_client.post(reverse("birthday_form"), {})
        assert response.status_code == 302

    def test_main_send_email_exception_handled(self, authenticated_client, db, group, parent):
        from students.models import Student, StudentParent

        today = date.today()
        s = Student.objects.create(
            first_name="Another",
            last_name="Kid",
            birth_date=date(2017, today.month, today.day),
            gdpr_signed=True,
            group=group,
            active=True,
        )
        StudentParent.objects.create(student=s, parent=parent)

        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.side_effect = Exception("smtp")
            response = authenticated_client.post(reverse("birthday_form"), {})
        assert response.status_code == 302


# ============================================================================
# Receipts form — all 3 types (quarterly_child, enrollment, adult)
# ============================================================================


class TestReceiptsFormExtra:
    def test_preview_quarterly_child(self, authenticated_client):
        response = authenticated_client.post(
            reverse("receipts_form"),
            {
                "action": "preview",
                "receipt_type": "quarterly_child",
                "month_1": "enero",
                "month_2": "febrero",
                "month_3": "marzo",
            },
        )
        assert response.status_code == 200
        assert "html" in response.json()

    def test_preview_enrollment(self, authenticated_client):
        response = authenticated_client.post(
            reverse("receipts_form"),
            {"action": "preview", "receipt_type": "enrollment"},
        )
        assert response.status_code == 200

    def test_preview_adult(self, authenticated_client):
        response = authenticated_client.post(
            reverse("receipts_form"),
            {"action": "preview", "receipt_type": "adult", "adult_month": "abril"},
        )
        assert response.status_code == 200

    def test_test_send_quarterly_child_with_env(self, authenticated_client, email_test_env, mock_email_svc):
        response = authenticated_client.post(
            reverse("receipts_form"),
            {
                "action": "test_send",
                "receipt_type": "quarterly_child",
                "month_1": "enero",
                "month_2": "febrero",
                "month_3": "marzo",
            },
        )
        assert response.json()["success"] is True

    def test_test_send_enrollment_with_env(self, authenticated_client, email_test_env, mock_email_svc):
        response = authenticated_client.post(
            reverse("receipts_form"),
            {"action": "test_send", "receipt_type": "enrollment"},
        )
        assert response.json()["success"] is True

    def test_test_send_adult_with_env(self, authenticated_client, email_test_env, mock_email_svc):
        response = authenticated_client.post(
            reverse("receipts_form"),
            {"action": "test_send", "receipt_type": "adult", "adult_month": "abril"},
        )
        assert response.json()["success"] is True

    def test_test_send_fail(self, authenticated_client, email_test_env):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = False
            response = authenticated_client.post(
                reverse("receipts_form"),
                {"action": "test_send", "receipt_type": "adult", "adult_month": "abril"},
            )
        assert response.json()["success"] is False

    def test_main_send_quarterly_child(self, authenticated_client, student_with_parent):
        with patch("core.views.app_forms.send_quarterly_receipt_email") as mock_send:
            mock_send.return_value = True
            response = authenticated_client.post(
                reverse("receipts_form"),
                {
                    "receipt_type": "quarterly_child",
                    "month_1": "enero",
                    "month_2": "febrero",
                    "month_3": "marzo",
                },
            )
        assert response.status_code == 302

    def test_main_send_quarterly_child_with_exceptions(self, authenticated_client, student_with_parent):
        with patch("core.views.app_forms.send_quarterly_receipt_email", side_effect=Exception("s")):
            response = authenticated_client.post(
                reverse("receipts_form"),
                {
                    "receipt_type": "quarterly_child",
                    "month_1": "enero",
                    "month_2": "febrero",
                    "month_3": "marzo",
                },
            )
        assert response.status_code == 302

    def test_main_send_enrollment(self, authenticated_client, student_with_parent):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = True
            response = authenticated_client.post(
                reverse("receipts_form"),
                {"receipt_type": "enrollment"},
            )
        assert response.status_code == 302

    def test_main_send_enrollment_exception(self, authenticated_client, student_with_parent):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.side_effect = Exception("s")
            response = authenticated_client.post(
                reverse("receipts_form"),
                {"receipt_type": "enrollment"},
            )
        assert response.status_code == 302

    def test_main_send_adult(self, authenticated_client, student_with_parent):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = True
            response = authenticated_client.post(
                reverse("receipts_form"),
                {"receipt_type": "adult", "adult_month": "abril"},
            )
        assert response.status_code == 302

    def test_main_send_adult_exception(self, authenticated_client, student_with_parent):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.side_effect = Exception("s")
            response = authenticated_client.post(
                reverse("receipts_form"),
                {"receipt_type": "adult", "adult_month": "abril"},
            )
        assert response.status_code == 302


# ============================================================================
# Newsletter form — test_send, main send, error paths
# ============================================================================


class TestNewsletterExtra:
    def test_get_renders(self, authenticated_client):
        response = authenticated_client.get(reverse("newsletter_form"))
        assert response.status_code == 200

    def test_preview_returns_html(self, authenticated_client):
        response = authenticated_client.post(
            reverse("newsletter_form"),
            {
                "action": "preview",
                "group_name": "Group A",
                "newsletter_link": "https://canva.com/123",
                "message": "Hello",
            },
        )
        assert response.status_code == 200
        assert "html" in response.json()

    def test_test_send_with_env(self, authenticated_client, email_test_env, mock_email_svc):
        response = authenticated_client.post(
            reverse("newsletter_form"),
            {
                "action": "test_send",
                "group_name": "Group A",
                "newsletter_link": "https://canva.com/x",
                "message": "Hi",
            },
        )
        assert response.json()["success"] is True

    def test_test_send_no_env(self, authenticated_client, monkeypatch):
        monkeypatch.delenv("EMAIL_TEST_1", raising=False)
        monkeypatch.delenv("EMAIL_TEST_2", raising=False)
        response = authenticated_client.post(
            reverse("newsletter_form"),
            {
                "action": "test_send",
                "group_name": "Group A",
                "newsletter_link": "x",
                "message": "y",
            },
        )
        assert response.json()["success"] is False

    def test_test_send_fail(self, authenticated_client, email_test_env):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = False
            response = authenticated_client.post(
                reverse("newsletter_form"),
                {"action": "test_send", "group_name": "G", "newsletter_link": "x", "message": "y"},
            )
        assert response.json()["success"] is False

    def test_main_send_missing_group(self, authenticated_client):
        response = authenticated_client.post(
            reverse("newsletter_form"),
            {"group_name": "", "newsletter_link": "x", "message": "y"},
        )
        assert response.status_code == 302

    def test_main_send_to_group(self, authenticated_client, student_with_parent, group):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = True
            response = authenticated_client.post(
                reverse("newsletter_form"),
                {
                    "group_name": group.group_name,
                    "newsletter_link": "https://canva.com/x",
                    "message": "Hello",
                },
            )
        assert response.status_code == 302

    def test_main_send_unknown_group_falls_back_to_all(self, authenticated_client, student_with_parent):
        """Unknown group_name → falls back to all active parents."""
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = True
            response = authenticated_client.post(
                reverse("newsletter_form"),
                {
                    "group_name": "NonExistentGroup",
                    "newsletter_link": "x",
                    "message": "y",
                },
            )
        assert response.status_code == 302

    def test_main_send_with_exception(self, authenticated_client, student_with_parent, group):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.side_effect = Exception("smtp")
            response = authenticated_client.post(
                reverse("newsletter_form"),
                {
                    "group_name": group.group_name,
                    "newsletter_link": "x",
                    "message": "y",
                },
            )
        assert response.status_code == 302

    def test_main_send_no_parent_emails(self, authenticated_client, db, group):
        from students.models import Parent, Student, StudentParent

        p = Parent.objects.create(
            first_name="No",
            last_name="Mail",
            dni="22222222B",
            phone="600222222",
            email="",
        )
        s = Student.objects.create(
            first_name="K",
            last_name="X",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
            active=True,
        )
        StudentParent.objects.create(student=s, parent=p)
        response = authenticated_client.post(
            reverse("newsletter_form"),
            {"group_name": group.group_name, "newsletter_link": "x", "message": "y"},
        )
        assert response.status_code == 302


# ============================================================================
# Enrollment form — welcome + enrollment (child + adult), main send
# ============================================================================


class TestEnrollmentFormExtra:
    def test_preview_welcome(self, authenticated_client, student_with_parent):
        response = authenticated_client.post(
            reverse("enrollment_form"),
            {
                "action": "preview",
                "email_type": "welcome",
                "student_id": student_with_parent.id,
            },
        )
        assert response.status_code == 200
        assert "html" in response.json()

    def test_preview_welcome_no_student(self, authenticated_client):
        """No student_id → uses default placeholder context."""
        response = authenticated_client.post(
            reverse("enrollment_form"),
            {"action": "preview", "email_type": "welcome"},
        )
        assert response.status_code == 200

    def test_preview_enrollment_child(self, authenticated_client, student):
        response = authenticated_client.post(
            reverse("enrollment_form"),
            {
                "action": "preview",
                "email_type": "enrollment",
                "enrollment_type": "child",
                "student_id": student.id,
            },
        )
        assert response.status_code == 200

    def test_preview_enrollment_adult(self, authenticated_client, adult_student):
        response = authenticated_client.post(
            reverse("enrollment_form"),
            {
                "action": "preview",
                "email_type": "enrollment",
                "enrollment_type": "adult",
                "student_id": adult_student.id,
            },
        )
        assert response.status_code == 200

    def test_test_send_welcome_with_env(self, authenticated_client, email_test_env, mock_email_svc):
        response = authenticated_client.post(
            reverse("enrollment_form"),
            {"action": "test_send", "email_type": "welcome"},
        )
        assert response.json()["success"] is True

    def test_test_send_welcome_no_env(self, authenticated_client, monkeypatch):
        monkeypatch.delenv("EMAIL_TEST_1", raising=False)
        monkeypatch.delenv("EMAIL_TEST_2", raising=False)
        response = authenticated_client.post(
            reverse("enrollment_form"),
            {"action": "test_send", "email_type": "welcome"},
        )
        assert response.json()["success"] is False

    def test_test_send_welcome_fail(self, authenticated_client, email_test_env):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = False
            response = authenticated_client.post(
                reverse("enrollment_form"),
                {"action": "test_send", "email_type": "welcome"},
            )
        assert response.json()["success"] is False

    def test_test_send_enrollment_with_env(self, authenticated_client, email_test_env, mock_email_svc):
        response = authenticated_client.post(
            reverse("enrollment_form"),
            {
                "action": "test_send",
                "email_type": "enrollment",
                "enrollment_type": "child",
            },
        )
        assert response.json()["success"] is True

    def test_test_send_enrollment_no_env(self, authenticated_client, monkeypatch):
        monkeypatch.delenv("EMAIL_TEST_1", raising=False)
        monkeypatch.delenv("EMAIL_TEST_2", raising=False)
        response = authenticated_client.post(
            reverse("enrollment_form"),
            {
                "action": "test_send",
                "email_type": "enrollment",
                "enrollment_type": "child",
            },
        )
        assert response.json()["success"] is False

    def test_test_send_enrollment_fail(self, authenticated_client, email_test_env):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = False
            response = authenticated_client.post(
                reverse("enrollment_form"),
                {
                    "action": "test_send",
                    "email_type": "enrollment",
                    "enrollment_type": "child",
                },
            )
        assert response.json()["success"] is False

    def test_main_send_missing_student_id(self, authenticated_client):
        response = authenticated_client.post(
            reverse("enrollment_form"),
            {"email_type": "enrollment"},
        )
        assert response.status_code == 302

    def test_main_send_welcome_success(self, authenticated_client, student_with_parent):
        with patch("core.views.app_forms.send_welcome_email") as mock_send:
            mock_send.return_value = True
            response = authenticated_client.post(
                reverse("enrollment_form"),
                {"email_type": "welcome", "student_id": student_with_parent.id},
            )
        assert response.status_code == 302

    def test_main_send_welcome_failure(self, authenticated_client, student_with_parent):
        with patch("core.views.app_forms.send_welcome_email") as mock_send:
            mock_send.return_value = False
            response = authenticated_client.post(
                reverse("enrollment_form"),
                {"email_type": "welcome", "student_id": student_with_parent.id},
            )
        assert response.status_code == 302

    def test_main_send_welcome_no_parent(self, authenticated_client, student):
        """Student without parent → error message."""
        response = authenticated_client.post(
            reverse("enrollment_form"),
            {"email_type": "welcome", "student_id": student.id},
        )
        assert response.status_code == 302

    def test_main_send_welcome_missing_student(self, authenticated_client):
        response = authenticated_client.post(
            reverse("enrollment_form"),
            {"email_type": "welcome", "student_id": "99999"},
        )
        assert response.status_code == 302

    def test_main_send_enrollment_child_success(self, authenticated_client, student_with_parent):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = True
            response = authenticated_client.post(
                reverse("enrollment_form"),
                {
                    "email_type": "enrollment",
                    "enrollment_type": "child",
                    "student_id": student_with_parent.id,
                    "gender": "m",
                    "academic_year": "2025-2026",
                    "month": "septiembre",
                },
            )
        assert response.status_code == 302

    def test_main_send_enrollment_adult_success(self, authenticated_client, adult_student, parent):
        from students.models import StudentParent

        StudentParent.objects.create(student=adult_student, parent=parent)
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = True
            response = authenticated_client.post(
                reverse("enrollment_form"),
                {
                    "email_type": "enrollment",
                    "enrollment_type": "adult",
                    "student_id": adult_student.id,
                    "gender": "m",
                },
            )
        assert response.status_code == 302

    def test_main_send_enrollment_failure(self, authenticated_client, student_with_parent):
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = False
            response = authenticated_client.post(
                reverse("enrollment_form"),
                {
                    "email_type": "enrollment",
                    "enrollment_type": "child",
                    "student_id": student_with_parent.id,
                },
            )
        assert response.status_code == 302

    def test_main_send_enrollment_no_parent(self, authenticated_client, student):
        response = authenticated_client.post(
            reverse("enrollment_form"),
            {
                "email_type": "enrollment",
                "enrollment_type": "child",
                "student_id": student.id,
            },
        )
        assert response.status_code == 302

    def test_main_send_enrollment_missing_student(self, authenticated_client):
        response = authenticated_client.post(
            reverse("enrollment_form"),
            {
                "email_type": "enrollment",
                "enrollment_type": "child",
                "student_id": "99999",
            },
        )
        assert response.status_code == 302


# ============================================================================
# welcome_form (it just redirects to enrollment_form)
# ============================================================================


class TestWelcomeFormRedirect:
    def test_welcome_form_redirects_to_enrollment_form(self, authenticated_client):
        response = authenticated_client.get(reverse("welcome_form"))
        assert response.status_code == 302
        assert response.url == reverse("enrollment_form")


# ============================================================================
# Extra coverage: small remaining edge cases
# ============================================================================


class TestAppFormsRemainingEdgeCases:
    def test_fun_friday_preview_with_missing_fields(self, authenticated_client):
        """Preview with only action set → uses all defaults, still renders."""
        response = authenticated_client.post(
            reverse("fun_friday_form"),
            {"action": "preview"},
        )
        assert response.status_code == 200

    def test_payment_reminder_invalid_fees_parse(self, authenticated_client):
        """Fees that aren't ints still render without crashing."""
        response = authenticated_client.post(
            reverse("payment_reminder_form"),
            {
                "payment_start_date": "2026-04-01",
                "payment_end_date": "2026-04-05",
                "month": "abril",
                "iban_number": "ES1234",
                "telephone_number_bizum": "600000000",
                "reduced_price_cheque_idioma": "34",
                "full_time_fee": "not-a-number",
                "part_time_fee": "also-not",
                "adult_fee": "nope",
            },
        )
        assert response.status_code in (200, 302)

    def test_apps_view_renders(self, authenticated_client):
        response = authenticated_client.get(reverse("apps"))
        assert response.status_code == 200
