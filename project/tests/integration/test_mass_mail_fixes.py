"""Regression tests for the v1.27 mass-mail fixes.

Everything here is a bug that shipped, so each test names the wrong behaviour it
pins shut rather than the code it happens to touch:

* families of waiting-list students received every mass mail;
* the Fun Friday counter, the recipients and the banner were three numbers;
* nothing deduplicated addresses, so a shared mailbox got two of everything;
* opening the SMTP connection 500'd the request instead of reporting a failure;
* the Fun Friday announcement was lost to a `DataError` on a seconds-precision
  time, and a double submit scheduled it twice;
* the cheque-idioma figure rendered as "34€ euros";
* the payment-history / group tables were drawn past the right edge of the page;
* the fiscal certificate merged same-named siblings into one subtotal.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from reportlab.lib.units import mm

from core.models import FunFridayScheduledSend, HistoryLog
from students.models import Parent, Student, StudentParent

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def waiting_family(db, group):
    """A student moved BACK onto the waiting list — they KEEP their parents.

    This is the leak: an entry taken over the phone has no `Parent` row at all,
    so `children__active=True` looked safe. `add_to_waiting_list` cancels the
    enrollment and leaves `active=True`, and every mass mail then reached a
    family whose child is not enrolled.
    """
    parent = Parent.objects.create(
        first_name="Elena",
        last_name="Espera",
        dni="99999999Z",
        phone="600999999",
        email="espera@test.com",
    )
    student = Student.objects.create(
        first_name="Niño",
        last_name="Espera",
        birth_date=date(2017, 4, 1),
        gdpr_signed=True,
        group=group,
        active=True,
        is_waiting=True,
    )
    StudentParent.objects.create(student=student, parent=parent)
    return parent


@pytest.fixture
def shared_mailbox_couple(db, group):
    """Two `Parent` rows on one address — legitimate, and not unique in the DB."""
    dad = Parent.objects.create(
        first_name="Luis", last_name="Buzón", dni="10000001A", phone="600100001", email="Buzon@test.com"
    )
    mum = Parent.objects.create(
        first_name="Rosa", last_name="Buzón", dni="10000002B", phone="600100002", email="buzon@TEST.com"
    )
    kid = Student.objects.create(
        first_name="Hijo",
        last_name="Buzón",
        birth_date=date(2016, 2, 2),
        gdpr_signed=True,
        group=group,
        active=True,
    )
    StudentParent.objects.create(student=kid, parent=dad)
    StudentParent.objects.create(student=kid, parent=mum)
    return dad, mum


def _addresses(outbox):
    return sorted({addr.lower() for message in outbox for addr in message.to})


# ---------------------------------------------------------------------------
# 1. Waiting-list families are out of every mass mail
# ---------------------------------------------------------------------------


class TestWaitingListFamiliesAreExcluded:
    def test_recipient_helper_drops_them(self, student_with_parent, waiting_family):
        from core.views.app_forms import _parent_recipients

        recipients = [r.lower() for r in _parent_recipients()]
        assert "maria@test.com" in recipients
        assert waiting_family.email.lower() not in recipients

    def test_payment_reminder_does_not_reach_them(self, authenticated_client, student_with_parent, waiting_family):
        response = authenticated_client.post(
            reverse("payment_reminder_form"),
            {
                "payment_start_date": "2026-04-01",
                "payment_end_date": "2026-04-05",
                "month": "abril",
                "iban_number": "ES1234567890",
                "telephone_number_bizum": "600000000",
            },
        )
        assert response.status_code == 302
        from django.core import mail

        assert waiting_family.email.lower() not in _addresses(mail.outbox)
        assert "maria@test.com" in _addresses(mail.outbox)

    def test_vacation_closure_does_not_reach_them(self, authenticated_client, student_with_parent, waiting_family):
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
        from django.core import mail

        assert waiting_family.email.lower() not in _addresses(mail.outbox)

    def test_fun_friday_does_not_schedule_them(self, authenticated_client, student_with_parent, waiting_family):
        friday = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 + 14)
        response = authenticated_client.post(
            reverse("fun_friday_form"),
            {
                "event_date": friday.isoformat(),
                "start_time": "17:00",
                "end_time": "18:30",
                "activity_description": "Manualidades",
                "min_age": "5",
                "max_age": "12",
            },
        )
        assert response.status_code == 302
        scheduled = FunFridayScheduledSend.objects.get()
        stored = [r.lower() for r in scheduled.recipients]
        assert waiting_family.email.lower() not in stored
        assert "maria@test.com" in stored

    def test_monthly_report_does_not_reach_them(self, authenticated_client, student_with_parent, waiting_family):
        with patch("core.views.app_forms.send_monthly_report", return_value=True) as mock_send:
            authenticated_client.post(reverse("monthly_report_form"), {"month": "abril", "year": "2026"})
        reached = {call.kwargs["recipient"].lower() for call in mock_send.call_args_list}
        assert waiting_family.email.lower() not in reached

    def test_birthday_cron_skips_them(self, group):
        """The daily job and the manual button must agree on who gets a card."""
        from comms.tasks import send_birthday_emails_task

        today = date.today()
        parent = Parent.objects.create(
            first_name="Cumple", last_name="Espera", dni="10000009X", phone="600100009", email="cumple@test.com"
        )
        waiting = Student.objects.create(
            first_name="Cumpleañero",
            last_name="Espera",
            birth_date=date(2016, today.month, today.day),
            gdpr_signed=True,
            group=group,
            active=True,
            is_waiting=True,
        )
        StudentParent.objects.create(student=waiting, parent=parent)

        result = send_birthday_emails_task.apply().get()
        assert result["birthdays_found"] == 0


# ---------------------------------------------------------------------------
# 2. One copy per mailbox, not per Parent row
# ---------------------------------------------------------------------------


class TestAddressDeduplication:
    def test_helper_is_case_insensitive(self):
        from core.views.app_forms import _dedupe_emails

        assert _dedupe_emails([" A@x.com ", "a@X.COM", "", None, "b@x.com"]) == ["A@x.com", "b@x.com"]

    def test_shared_mailbox_gets_one_vacation_notice(self, authenticated_client, shared_mailbox_couple):
        from django.core import mail

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
        to_buzon = [m for m in mail.outbox if any("buzon@test.com" == a.lower() for a in m.to)]
        assert len(to_buzon) == 1, "two Parent rows on one address must not mean two emails"

    def test_shared_mailbox_gets_one_monthly_report(self, authenticated_client, shared_mailbox_couple):
        with patch("core.views.app_forms.send_monthly_report", return_value=True) as mock_send:
            authenticated_client.post(reverse("monthly_report_form"), {"month": "abril", "year": "2026"})
        reached = [call.kwargs["recipient"].lower() for call in mock_send.call_args_list]
        assert reached.count("buzon@test.com") == 1

    def test_shared_mailbox_gets_one_receipt_per_child(self, authenticated_client, shared_mailbox_couple):
        with patch("core.views.app_forms.send_quarterly_receipt_email", return_value=True) as mock_send:
            authenticated_client.post(
                reverse("receipts_form"),
                {"receipt_type": "quarterly_child", "month_1": "enero", "month_2": "febrero", "month_3": "marzo"},
            )
        # One child, one shared address → exactly one receipt (was two).
        assert mock_send.call_count == 1


# ---------------------------------------------------------------------------
# 3. Fun Friday: the counted set IS the sent set
# ---------------------------------------------------------------------------


class TestFunFridayRecipientCount:
    def test_count_matches_what_is_scheduled(self, authenticated_client, student_with_parent, adult_student):
        """The counter excluded adult students and the send did not."""
        adult_parent = Parent.objects.create(
            first_name="Solo", last_name="Adulto", dni="10000003C", phone="600100003", email="adulto@test.com"
        )
        StudentParent.objects.create(student=adult_student, parent=adult_parent)

        page = authenticated_client.get(reverse("fun_friday_form"))
        counted = page.context["parent_count"]

        friday = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 + 14)
        authenticated_client.post(
            reverse("fun_friday_form"),
            {
                "event_date": friday.isoformat(),
                "start_time": "17:00",
                "end_time": "18:30",
                "activity_description": "Manualidades",
                "min_age": "5",
                "max_age": "12",
            },
        )
        scheduled = FunFridayScheduledSend.objects.get()
        assert counted == len(scheduled.recipients)
        # A children's activity with a min/max age does not go to the parent of
        # an adult student.
        assert "adulto@test.com" not in [r.lower() for r in scheduled.recipients]

    def test_count_ignores_parents_without_an_address(self, authenticated_client, student_with_parent, group):
        no_mail = Parent.objects.create(
            first_name="Sin", last_name="Correo", dni="10000004D", phone="600100004", email=""
        )
        kid = Student.objects.create(
            first_name="Hijo",
            last_name="SinCorreo",
            birth_date=date(2016, 1, 1),
            gdpr_signed=True,
            group=group,
            active=True,
        )
        StudentParent.objects.create(student=kid, parent=no_mail)

        page = authenticated_client.get(reverse("fun_friday_form"))
        assert page.context["parent_count"] == 1, "a parent with no email is not a reachable recipient"


# ---------------------------------------------------------------------------
# 4. An SMTP outage is a warning, not a 500
# ---------------------------------------------------------------------------


class TestSmtpConnectionFailureIsReported:
    @pytest.fixture
    def birthday_child(self, group, parent):
        today = date.today()
        student = Student.objects.create(
            first_name="Cumple",
            last_name="Hoy",
            birth_date=date(2015, today.month, today.day),
            gdpr_signed=True,
            group=group,
            active=True,
        )
        StudentParent.objects.create(student=student, parent=parent)
        return student

    def test_birthday_send_does_not_500(self, authenticated_client, birthday_child):
        """`with email_service.open_connection()` calls open() with
        fail_silently=False, so a dead SMTP hop crashed the whole POST."""
        with patch("core.views.app_forms.email_service.open_connection", side_effect=OSError("smtp down")):
            response = authenticated_client.post(reverse("birthday_form"), {})
        assert response.status_code == 302

    def test_the_outage_is_recorded(self, authenticated_client, birthday_child):
        with patch("core.views.app_forms.email_service.open_connection", side_effect=OSError("smtp down")):
            authenticated_client.post(reverse("birthday_form"), {})
        assert HistoryLog.objects.filter(action="email_sent", message__contains="fallidos").exists()

    def test_newsletter_send_does_not_500(self, authenticated_client, student_with_parent, group):
        with patch("core.views.app_forms.email_service.open_connection", side_effect=OSError("smtp down")):
            response = authenticated_client.post(
                reverse("newsletter_form"),
                {"group_name": group.group_name, "newsletter_link": "https://canva.com/x", "message": "Hola"},
            )
        assert response.status_code == 302

    def test_tax_certificates_report_failure_instead_of_raising(self, completed_payment):
        from comms.services.email_functions import send_all_tax_certificates

        with patch("comms.services.email_service.email_service.open_connection", side_effect=OSError("smtp down")):
            results = send_all_tax_certificates(2025)
        assert results["sent"] == 0
        assert results["failed"] == 1


class TestTotalFailureIsRecorded:
    def test_history_entry_written_even_when_nothing_was_sent(self, authenticated_client, student_with_parent):
        """`HistoryLog` used to be written only when success_count > 0, so a
        total mail outage left no trace in the activity feed at all."""
        with patch("core.views.app_forms.email_service") as svc:
            svc.send_email.return_value = False
            authenticated_client.post(reverse("receipts_form"), {"receipt_type": "enrollment"})
        entry = HistoryLog.objects.filter(action="email_sent", message__startswith="Recibos").first()
        assert entry is not None
        assert "fallidos" in entry.message


# ---------------------------------------------------------------------------
# 5. The Fun Friday announcement is validated and cannot double-submit
# ---------------------------------------------------------------------------


class TestFunFridayScheduledSendValidation:
    def _payload(self, friday, **overrides):
        payload = {
            "event_date": friday.isoformat(),
            "start_time": "17:00",
            "end_time": "18:30",
            "activity_description": "Manualidades con material reciclado",
            "min_age": "5",
            "max_age": "12",
            "meeting_point": "Puerta principal",
        }
        payload.update(overrides)
        return payload

    def _future_friday(self):
        today = date.today()
        return today + timedelta(days=(4 - today.weekday()) % 7 + 14)

    def test_seconds_precision_time_is_normalised(self, authenticated_client, student_with_parent):
        """`start_time` is a CharField(max_length=5); "17:00:00" was a DataError
        (a 500 that also threw away the composed announcement)."""
        friday = self._future_friday()
        response = authenticated_client.post(
            reverse("fun_friday_form"),
            self._payload(friday, start_time="17:00:00", end_time="18:30:00"),
        )
        assert response.status_code == 302
        scheduled = FunFridayScheduledSend.objects.get()
        assert (scheduled.start_time, scheduled.end_time) == ("17:00", "18:30")

    def test_unparseable_time_re_renders_with_the_text_preserved(self, authenticated_client, student_with_parent):
        friday = self._future_friday()
        response = authenticated_client.post(
            reverse("fun_friday_form"),
            self._payload(friday, start_time="a las cinco"),
        )
        assert response.status_code == 200
        assert not FunFridayScheduledSend.objects.exists()
        assert "Manualidades con material reciclado" in response.context["default_html"]

    def test_double_submit_schedules_once(self, authenticated_client, student_with_parent):
        friday = self._future_friday()
        payload = self._payload(friday)
        first = authenticated_client.post(reverse("fun_friday_form"), payload)
        second = authenticated_client.post(reverse("fun_friday_form"), payload)
        assert (first.status_code, second.status_code) == (302, 302)
        assert FunFridayScheduledSend.objects.count() == 1

    def test_negative_age_is_refused_not_persisted(self, authenticated_client, student_with_parent):
        """`objects.create()` does not validate; a PositiveSmallIntegerField was
        handed a negative value straight from POST."""
        friday = self._future_friday()
        response = authenticated_client.post(
            reverse("fun_friday_form"),
            self._payload(friday, min_age="-3", max_age="12"),
        )
        assert response.status_code == 200
        assert not FunFridayScheduledSend.objects.exists()


# ---------------------------------------------------------------------------
# 6. Preview and send pick the same guardian
# ---------------------------------------------------------------------------


class TestGuardianResolutionIsShared:
    def test_preview_names_the_parent_who_receives_the_mail(self, authenticated_client, student, parent, second_parent):
        StudentParent.objects.create(student=student, parent=parent)
        StudentParent.objects.create(student=student, parent=second_parent)

        preview = authenticated_client.post(
            reverse("enrollment_form"),
            {"action": "preview", "email_type": "welcome", "student_id": student.id},
        ).json()["html"]

        with patch("core.views.app_forms.send_welcome_email", return_value=True) as mock_send:
            authenticated_client.post(
                reverse("enrollment_form"),
                {"email_type": "welcome", "student_id": student.id},
            )
        assert mock_send.call_args.kwargs["parent_name"] in preview

    def test_waiting_students_are_not_offered_for_matriculation(self, authenticated_client, waiting_family):
        page = authenticated_client.get(reverse("enrollment_form"))
        names = [s.first_name for s in page.context["students"]]
        assert "Niño" not in names


# ---------------------------------------------------------------------------
# 7. "34€ euros"
# ---------------------------------------------------------------------------


class TestChequeIdiomaUnit:
    def test_default_is_derived_and_carries_no_currency_symbol(self, authenticated_client, site_config):
        page = authenticated_client.get(reverse("payment_reminder_form"))
        # 54,00 full-time fee − 20,00 language-cheque discount, formatted like
        # every other row of the tarifas table.
        assert page.context["default_cheque_price"] == "34"
        assert "34 euros" in page.context["email_html"]
        assert "€ euros" not in page.context["email_html"]

    def test_an_operator_typed_symbol_is_stripped(self, authenticated_client, student_with_parent):
        from django.core import mail

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
        body = mail.outbox[0].alternatives[0][0]
        assert "34 euros" in body
        assert "34€ euros" not in body

    def test_blank_input_falls_back_to_site_configuration(self, authenticated_client, student_with_parent):
        from django.core import mail

        authenticated_client.post(
            reverse("payment_reminder_form"),
            {
                "payment_start_date": "2026-04-01",
                "payment_end_date": "2026-04-05",
                "month": "abril",
                "iban_number": "ES1234567890",
                "telephone_number_bizum": "600000000",
                "reduced_price_cheque_idioma": "",
            },
        )
        body = mail.outbox[0].alternatives[0][0]
        # Never the old hard-coded "34€" literal on the POST path.
        assert "34 euros" in body

    def test_helper_tracks_configuration(self, site_config):
        from comms.services.email_functions import cheque_idioma_fee

        site_config.full_time_monthly_fee = Decimal("60.00")
        site_config.language_cheque_discount = Decimal("20.00")
        site_config.save()
        assert cheque_idioma_fee() == "40"


# ---------------------------------------------------------------------------
# 8. PDF layout
# ---------------------------------------------------------------------------


def _spy_pdf(fn, *args, **kwargs):
    """Run a PDF generator, recording every Table and Paragraph it builds."""
    from billing.services import pdf_service

    tables, paragraphs = [], []
    real_table, real_paragraph = pdf_service.Table, pdf_service.Paragraph

    def table_spy(*a, **kw):
        built = real_table(*a, **kw)
        tables.append(built)
        return built

    def paragraph_spy(text, *a, **kw):
        paragraphs.append(text)
        return real_paragraph(text, *a, **kw)

    with patch.object(pdf_service, "Table", table_spy), patch.object(pdf_service, "Paragraph", paragraph_spy):
        result = fn(*args, **kwargs)
    return result, tables, paragraphs


def _assert_tables_fit(tables):
    from billing.services.pdf_service import _FRAME_WIDTH_MM

    frame = _FRAME_WIDTH_MM * mm
    for table in tables:
        width, _height = table.wrap(frame, 10_000)
        assert width <= frame + 1e-6, f"table is {width / mm:.1f} mm wide inside a {_FRAME_WIDTH_MM} mm frame"


class TestPdfTablesFitThePage:
    def test_payment_history_amount_column_is_not_cut_off(self, student, completed_payment, pending_payment):
        from billing.services.pdf_service import generate_student_payment_history

        pdf, tables, _ = _spy_pdf(generate_student_payment_history, student, [completed_payment, pending_payment])
        assert pdf[:4] == b"%PDF"
        _assert_tables_fit(tables)

    def test_report_group_table_fits(self, site_config):
        from billing.services.pdf_service import generate_report_pdf

        report = {
            "current_month": {
                "income": Decimal("100"),
                "pending": Decimal("10"),
                "expenses": Decimal("5"),
                "net": Decimal("95"),
            },
            "collection": {"expected": Decimal("110"), "collected": Decimal("100"), "percent": 91},
            "retention": {"baseline": 3, "still_active": 2, "retention_percent": 67},
            "groups": [
                {
                    "name": "Group A",
                    "teacher": "Ana García",
                    "enrolled": 5,
                    "max_students": 8,
                    "utilisation_percent": 63,
                    "waiting": 1,
                },
                {
                    "name": "Group B",
                    "teacher": "Ana García",
                    "enrolled": 4,
                    "max_students": 0,
                    "utilisation_percent": 0,
                    "waiting": 0,
                },
            ],
        }
        pdf, tables, _ = _spy_pdf(generate_report_pdf, report, 9, 2026)
        assert pdf[:4] == b"%PDF"
        _assert_tables_fit(tables)

    def test_receipt_and_certificate_fit(self, completed_payment, parent):
        from billing.services.pdf_service import generate_payment_receipt, generate_tax_certificate

        _pdf, tables, _ = _spy_pdf(generate_payment_receipt, completed_payment)
        _assert_tables_fit(tables)
        _pdf, tables, _ = _spy_pdf(generate_tax_certificate, parent, 2025)
        _assert_tables_fit(tables)

    def test_clamp_keeps_a_too_wide_table_inside_the_frame(self):
        from billing.services.pdf_service import _FRAME_WIDTH_MM, _fit_widths

        widths = _fit_widths([200, 100])
        assert sum(widths) <= _FRAME_WIDTH_MM * mm + 1e-6


class TestTaxCertificateGroupsByStudentId:
    def test_same_named_siblings_are_not_merged(self, parent, group, active_enrollment, student):
        """Grouped by NAME, two siblings called the same thing shared one
        subtotal on a document the family files with the tax authority."""
        from billing.models import Payment
        from billing.services.pdf_service import generate_tax_certificate

        twin = Student.objects.create(
            first_name=student.first_name,
            last_name=student.last_name,
            birth_date=date(2019, 6, 1),
            gdpr_signed=True,
            group=group,
            active=True,
        )
        StudentParent.objects.create(student=twin, parent=parent)
        for who in (student, twin):
            Payment.objects.create(
                student=who,
                parent=parent,
                payment_type="monthly",
                payment_method="transfer",
                amount=Decimal("54.00"),
                payment_status="completed",
                due_date=date(2025, 10, 1),
                payment_date=date(2025, 10, 3),
                concept="Mensualidad Octubre 2025",
            )

        _pdf, tables, paragraphs = _spy_pdf(generate_tax_certificate, parent, 2025)
        headings = [p for p in paragraphs if isinstance(p, str) and p.startswith("<b>Estudiante:</b>")]
        assert len(headings) == 2, "one block per STUDENT, even when two share a name"
        assert headings[0] == headings[1], "and both blocks legitimately carry the same name"
        # Two per-student tables (each with its own Subtotal row) + the grand
        # total banner. Grouped by name it was one table plus the banner.
        assert len(tables) == 3
