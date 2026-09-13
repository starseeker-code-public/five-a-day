"""Who may collect a child, what a receipt line says, and which reminder a month gets.

Four things QA reported in the first weeks of the 2026-2027 course (v1.29.3).
They share a shape rather than a module: each is a figure or a field the
academy shows a FAMILY, which the app was either not storing, describing
wrongly, or unable to send at all.

* `Student.pickup_authorized` — the ficha had nowhere to record the people a
  family authorises to collect their child.
* The receipt's **Cheque Idioma** line read `−10,67 €` on a prorated first
  month because the flat cheque was scaled with the month. The total was right
  either way, so only a family holding a 20 € cheque would ever have caught it.
* **"Enviar prueba"** on the ten mail forms required `EMAIL_TEST_*`, which the
  QA VM has never defined — so the button was dead exactly where QA uses it.
* The payment reminder quoted twelve identical months. September bills half a
  month, June carries the fin-de-curso discount on every monthly fee, and April
  is where a quarterly family sees that same discount.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse

from billing.constants import SEPTEMBER_CLASSES_START_DAY
from billing.models import Enrollment, Payment
from billing.services.payment_service import PaymentService
from billing.services.pricing_service import PricingService, _euros
from core.constants import MESES_ES
from students.models import Student

pytestmark = pytest.mark.django_db


# ============================================================================
# Cheque idioma on the receipt
# ============================================================================


def _cheque_enrollment(student, enrollment_type, *, start, modality="monthly"):
    return Enrollment.objects.create(
        student=student,
        enrollment_type=enrollment_type,
        enrollment_period_start=date(2026, 9, 15),
        enrollment_period_end=date(2027, 6, 27),
        academic_year="2026-2027",
        schedule_type="full_time",
        payment_modality=modality,
        has_language_cheque=True,
        enrollment_amount=Decimal("54.00"),
        discount_percentage=Decimal("0.00"),
        final_amount=Decimal("34.00"),
        status="active",
        enrollment_date=start,
    )


def _period_payment(student, enrollment, period, config, *, quarterly):
    months = [m for m, _ in period["months"]]
    amount = PaymentService.calculate_period_amount(enrollment, config, months, period["fraction"], quarterly=quarterly)
    return Payment.objects.create(
        student=student,
        parent=student.parents.first(),
        enrollment=enrollment,
        payment_type="quarterly" if quarterly else "monthly",
        payment_method="transfer",
        amount=amount,
        payment_status="completed",
        due_date=period["due"],
        payment_date=period["due"],
        concept="Cuota",
    )


def _shown(rows):
    return [Decimal(r[1].replace("−", "-").replace(" €", "").strip()) for r in rows]


class TestChequeIdiomaLineIsWholeCheques:
    """QA: the receipt's Cheque Idioma line must read −20 €, always.

    On a family's first, prorated September receipt it read "−10,67 €": the
    cheque was scaled by the same 16/30 as the month. The total was right —
    `(base − 20) × f` and `base × f − 20 × f` are the same number — but the
    line described a cheque nobody had been given. The cheque now comes off
    the FULL period before the proration, which scales what is left.
    """

    def test_prorated_september_shows_minus_twenty(self, student_with_parent, enrollment_type_new_student, site_config):
        from billing.services.pdf_service import _receipt_breakdown_rows

        enrollment = _cheque_enrollment(student_with_parent, enrollment_type_new_student, start=date(2026, 9, 15))
        period = PaymentService.billing_periods(enrollment)[0]
        assert period["fraction"] == Decimal(16) / Decimal(30), "sanity: 15 Sep on a 30-day month is 16/30"
        payment = _period_payment(student_with_parent, enrollment, period, site_config, quarterly=False)

        rows = _receipt_breakdown_rows(payment)
        by_label = dict(rows)
        assert by_label["Cheque idioma"] == f"−{site_config.language_cheque_discount:.2f} €"
        assert "Prorrateo primer periodo" in by_label
        # The column still adds up to the importe.
        assert sum(_shown(rows)) == payment.amount

    def test_total_is_unchanged_by_the_reordering(self, student_with_parent, enrollment_type_new_student, site_config):
        """Cheque-before-proration must bill exactly what cheque-after did."""
        enrollment = _cheque_enrollment(student_with_parent, enrollment_type_new_student, start=date(2026, 9, 15))
        fraction = Decimal(16) / Decimal(30)
        billed = PaymentService.calculate_period_amount(enrollment, site_config, [9], fraction)

        base = site_config.full_time_monthly_fee
        cheque = site_config.language_cheque_discount
        old_order = PaymentService._round_money(base * fraction - cheque * fraction)
        new_order = PaymentService._round_money((base - cheque) * fraction)
        assert billed == old_order == new_order

    def test_cheque_precedes_proration_in_the_lines(
        self, student_with_parent, enrollment_type_new_student, site_config
    ):
        enrollment = _cheque_enrollment(student_with_parent, enrollment_type_new_student, start=date(2026, 9, 15))
        lines, _total = PaymentService.price_breakdown(enrollment, site_config, [9], Decimal(16) / Decimal(30), False)
        labels = [label for label, _ in lines]
        assert labels.index("Cheque idioma") < labels.index("Prorrateo primer periodo")

    def test_a_full_quarter_shows_three_cheques(self, student_with_parent, enrollment_type_new_student, site_config):
        """A quarter spans three months, so it carries three cheques — labelled as such."""
        from billing.services.pdf_service import _receipt_breakdown_rows

        enrollment = _cheque_enrollment(
            student_with_parent, enrollment_type_new_student, start=date(2026, 9, 1), modality="quarterly"
        )
        period = PaymentService.billing_periods(enrollment)[0]
        assert period["fraction"] == Decimal("1")
        payment = _period_payment(student_with_parent, enrollment, period, site_config, quarterly=True)

        rows = dict(_receipt_breakdown_rows(payment))
        assert rows["Cheque idioma (3 meses)"] == f"−{site_config.language_cheque_discount * 3:.2f} €"
        assert "Prorrateo primer periodo" not in rows

    def test_june_stub_takes_the_cheque_and_the_june_discount(
        self, student_with_parent, enrollment_type_new_student, site_config
    ):
        enrollment = _cheque_enrollment(
            student_with_parent, enrollment_type_new_student, start=date(2026, 9, 1), modality="quarterly"
        )
        lines, total = PaymentService.price_breakdown(enrollment, site_config, [6], Decimal("1"), True)
        by_label = dict(lines)
        assert by_label["Cheque idioma"] == site_config.language_cheque_discount
        assert by_label["Descuento junio (curso completo)"] == site_config.june_discount
        assert lines[0][1] - sum(v for _, v in lines[1:]) == total


# ============================================================================
# "Enviar prueba" recipients
# ============================================================================


def _reminder_payload(**extra):
    return {
        "action": "test_send",
        "payment_start_date": "2026-10-01",
        "payment_end_date": "2026-10-05",
        "month": "octubre",
        "iban_number": "ES1234",
        "telephone_number_bizum": "600000000",
        **extra,
    }


@pytest.fixture
def no_test_env(monkeypatch, settings):
    monkeypatch.delenv("EMAIL_TEST_1", raising=False)
    monkeypatch.delenv("EMAIL_TEST_2", raising=False)
    settings.SUPPORT_EMAIL = None


@pytest.fixture
def mock_email_svc():
    with patch("core.views.app_forms.email_service") as svc:
        svc.send_email.return_value = True
        yield svc


class TestTestSendFallsBackToSomeoneReal:
    """QA: "no funcionan los correos de prueba".

    The button only ever mailed `EMAIL_TEST_1` / `EMAIL_TEST_2`, and the QA VM's
    `.env` does not define them, so every one of the ten forms answered
    "no configurados". A test send is a diagnostic — it must reach the person
    pressing the button.
    """

    def test_env_vars_still_win_when_set(self, authenticated_client, monkeypatch, mock_email_svc, settings):
        monkeypatch.setenv("EMAIL_TEST_1", "qa1@test.local")
        monkeypatch.setenv("EMAIL_TEST_2", "qa2@test.local")
        settings.SUPPORT_EMAIL = "soporte@test.local"
        response = authenticated_client.post(reverse("payment_reminder_form"), _reminder_payload())
        assert response.json()["success"] is True
        assert mock_email_svc.send_email.call_args.kwargs["recipients"] == ["qa1@test.local", "qa2@test.local"]

    def test_logged_in_teacher_receives_it(self, client, no_test_env, mock_email_svc):
        user = User.objects.create_superuser(username="qa@fiveaday.test", email="qa@fiveaday.test", password="x")
        client.force_login(user)
        session = client.session
        session["is_authenticated"] = True
        session["username"] = user.username
        session.save()

        response = client.post(reverse("payment_reminder_form"), _reminder_payload())
        data = response.json()
        assert data["success"] is True
        assert "qa@fiveaday.test" in data["message"]
        assert mock_email_svc.send_email.call_args.kwargs["recipients"] == ["qa@fiveaday.test"]

    def test_support_email_is_the_last_resort(self, authenticated_client, no_test_env, mock_email_svc, settings):
        settings.SUPPORT_EMAIL = "soporte@test.local"
        response = authenticated_client.post(reverse("payment_reminder_form"), _reminder_payload())
        assert response.json()["success"] is True
        assert mock_email_svc.send_email.call_args.kwargs["recipients"] == ["soporte@test.local"]

    def test_nothing_configured_says_what_to_configure(self, authenticated_client, no_test_env, mock_email_svc):
        response = authenticated_client.post(reverse("payment_reminder_form"), _reminder_payload())
        data = response.json()
        assert data["success"] is False
        assert "EMAIL_TEST_1" in data["message"] and "SUPPORT_EMAIL" in data["message"]
        mock_email_svc.send_email.assert_not_called()

    def test_every_form_threads_the_request(self, authenticated_client, no_test_env, mock_email_svc, settings):
        """The fallback lives in one helper; every form must reach it with the
        request, or that form alone keeps failing on the VM."""
        settings.SUPPORT_EMAIL = "soporte@test.local"
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
        assert mock_email_svc.send_email.call_args.kwargs["recipients"] == ["soporte@test.local"]


# ============================================================================
# Autorizados para la recogida
# ============================================================================


class TestPickupAuthorized:
    """QA: the new-student form needs somewhere to write who may collect the child."""

    PEOPLE = "Abuela Carmen García — 11111111H\nTío Luis García (sin DNI)"

    def test_form_accepts_it(self, group):
        from students.forms import StudentForm

        form = StudentForm(
            data={
                "first_name": "Lucía",
                "last_name": "Pérez",
                "birth_date": "2018-03-10",
                "group": group.id,
                "pickup_authorized": self.PEOPLE,
            }
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["pickup_authorized"] == self.PEOPLE

    def test_it_is_optional(self, group):
        from students.forms import StudentForm

        form = StudentForm(data={"first_name": "Lucía", "last_name": "Pérez", "group": group.id})
        assert form.is_valid(), form.errors

    def test_create_view_persists_it(
        self, authenticated_client, parent, group, site_config, enrollment_type_new_student
    ):
        response = authenticated_client.post(
            reverse("student_create") + f"?parent_id={parent.id}",
            {
                "first_name": "Recogida",
                "last_name": "Alumna",
                "birth_date": "2018-03-10",
                "school": "CEIP Nuevo",
                "gdpr_signed": "on",
                "group": group.id,
                "parent_id": parent.id,
                "enrollment_plan": "monthly_full",
                "pickup_authorized": self.PEOPLE,
            },
        )
        assert response.status_code == 302
        assert Student.objects.get(first_name="Recogida").pickup_authorized == self.PEOPLE

    def test_create_page_renders_the_field_for_children_only(
        self, authenticated_client, group, site_config, enrollment_type_new_student
    ):
        child_page = authenticated_client.get(reverse("student_create")).content.decode()
        adult_page = authenticated_client.get(reverse("student_create") + "?mode=adult").content.decode()
        assert 'name="pickup_authorized"' in child_page
        assert 'name="pickup_authorized"' not in adult_page

    def test_detail_page_shows_it(self, authenticated_client, student_with_parent):
        student_with_parent.pickup_authorized = self.PEOPLE
        student_with_parent.save(update_fields=["pickup_authorized"])
        page = authenticated_client.get(reverse("student_detail", args=[student_with_parent.id])).content.decode()
        assert "Autorizados recogida" in page
        assert "Abuela Carmen García" in page

    def test_update_view_saves_it(self, authenticated_client, student_with_parent, active_enrollment, group):
        response = authenticated_client.post(
            reverse("student_update", args=[student_with_parent.id]),
            {
                "first_name": student_with_parent.first_name,
                "last_name": student_with_parent.last_name,
                "birth_date": "2018-05-15",
                "school": "CEIP Test",
                "gdpr_signed": "on",
                "group": group.id,
                "pickup_authorized": self.PEOPLE,
                "enrollment_plan": "monthly_full",
            },
        )
        assert response.status_code in (302, 200)
        student_with_parent.refresh_from_db()
        assert student_with_parent.pickup_authorized == self.PEOPLE

    def test_admin_can_reach_it(self):
        from students.admin import StudentAdmin

        fields = [f for _, opts in StudentAdmin.fieldsets for f in opts["fields"]]
        assert "pickup_authorized" in fields


# ============================================================================
# September / June / April reminder emails
# ============================================================================


class TestReminderSpecialMonths:
    """`payment_reminder_special` is the one source of the three special emails."""

    def test_a_regular_month_is_the_regular_email(self, site_config):
        ctx = PricingService.payment_reminder_special(site_config, 10)
        assert ctx["special_case"] is None
        assert ctx["template_name"] == "payment_reminder"
        assert ctx["subject_suffix"] == ""
        assert ctx["full_time_fee"] == _euros(site_config.full_time_monthly_fee)
        assert ctx["part_time_child_fee"] == _euros(site_config.part_time_child_monthly_fee)
        assert ctx["reduced_price_cheque_idioma"] == _euros(
            site_config.full_time_monthly_fee - site_config.language_cheque_discount
        )

    def test_september_prorates_the_monthly_rows_like_the_generator(self, site_config):
        ctx = PricingService.payment_reminder_special(site_config, 9)
        fraction = PaymentService.proration_fraction(date(2026, 9, SEPTEMBER_CLASSES_START_DAY), 9, 2026)
        assert ctx["template_name"] == "payment_reminder_september"
        assert ctx["september_start_day"] == SEPTEMBER_CLASSES_START_DAY
        assert ctx["proration_percent"] == 50
        assert ctx["full_time_fee"] == _euros(PaymentService._round_money(site_config.full_time_monthly_fee * fraction))
        assert ctx["part_time_fee"] == _euros(PaymentService._round_money(site_config.part_time_monthly_fee * fraction))
        assert ctx["part_time_child_fee"] == _euros(
            PaymentService._round_money(site_config.part_time_child_monthly_fee * fraction)
        )
        assert ctx["adult_fee"] == _euros(PaymentService._round_money(site_config.adult_group_monthly_fee * fraction))
        # Cheque idioma: whole cheque, then the proration — the receipt's order.
        cheque_net = (site_config.full_time_monthly_fee - site_config.language_cheque_discount) * fraction
        assert ctx["reduced_price_cheque_idioma"] == _euros(PaymentService._round_money(cheque_net))
        # The quarter is unchanged, and the regular figures are still available.
        assert ctx["quarterly_fee"] == ctx["standard_quarterly_fee"]
        assert ctx["standard_full_time_fee"] == _euros(site_config.full_time_monthly_fee)

    def test_the_default_september_is_exactly_half_the_month(self, site_config):
        """The copy says "solo se cobra medio mes"; the figures must agree.

        September has 30 days and `proration_fraction` counts the joining day,
        so the default start day has to be the 16th (15/30). The 15th bills
        16/30 = 53 %, which is what this email used to quote underneath a
        paragraph promising half a month.
        """
        ctx = PricingService.payment_reminder_special(site_config, 9)
        assert ctx["proration_percent"] == 50
        assert ctx["full_time_fee"] == _euros(site_config.full_time_monthly_fee / 2)
        assert ctx["part_time_fee"] == _euros(site_config.part_time_monthly_fee / 2)

    def test_september_start_day_can_be_overridden_and_is_validated(self, site_config):
        ctx = PricingService.payment_reminder_special(site_config, 9, september_start_day="15")
        assert ctx["september_start_day"] == 15
        assert ctx["proration_percent"] == 53
        for bad in ("", "abc", "0", "31", None):
            assert (
                PricingService.payment_reminder_special(site_config, 9, september_start_day=bad)["september_start_day"]
                == SEPTEMBER_CLASSES_START_DAY
            )

    def test_june_takes_the_fin_de_curso_discount_off_every_monthly_fee(self, site_config):
        ctx = PricingService.payment_reminder_special(site_config, 6)
        june = site_config.june_discount
        assert ctx["template_name"] == "payment_reminder_june"
        assert ctx["full_time_fee"] == _euros(site_config.full_time_monthly_fee - june)
        assert ctx["part_time_fee"] == _euros(site_config.part_time_monthly_fee - june)
        assert ctx["part_time_child_fee"] == _euros(site_config.part_time_child_monthly_fee - june)
        assert ctx["sibling_full_time_fee"] == _euros(
            PaymentService._round_money(PricingService.calculate_sibling_price(site_config) - june)
        )
        assert ctx["reduced_price_cheque_idioma"] == _euros(
            site_config.full_time_monthly_fee - site_config.language_cheque_discount - june
        )
        # Adults never get it; the quarter had it in April.
        assert ctx["adult_fee"] == _euros(site_config.adult_group_monthly_fee)
        assert ctx["quarterly_fee"] == ctx["standard_quarterly_fee"]
        assert ctx["june_discount"] == _euros(june)

    def test_april_discounts_only_the_quarter(self, site_config):
        ctx = PricingService.payment_reminder_special(site_config, 4)
        assert ctx["template_name"] == "payment_reminder_april"
        assert ctx["quarterly_fee"] == _euros(
            PricingService.calculate_quarterly_price(site_config) - site_config.june_discount
        )
        assert ctx["full_time_fee"] == ctx["standard_full_time_fee"]
        assert ctx["reduced_price_cheque_idioma"] == ctx["standard_reduced_price_cheque_idioma"]

    def test_figures_are_the_generators_own(self, student, enrollment_type_new_student, site_config):
        """Every special figure must equal what `calculate_period_amount` bills
        an enrollment with those flags — never a `fee − 20` typed in the service."""
        enrollment = Enrollment(
            student=student,
            enrollment_type=enrollment_type_new_student,
            academic_year="2026-2027",
            schedule_type="full_time",
            payment_modality="monthly",
            has_language_cheque=True,
            enrollment_amount=Decimal("0.00"),
            discount_percentage=Decimal("0.00"),
            final_amount=Decimal("0.00"),
            status="active",
        )
        june = PricingService.payment_reminder_special(site_config, 6)
        assert june["reduced_price_cheque_idioma"] == _euros(
            PaymentService.calculate_period_amount(enrollment, site_config, [6])
        )
        september = PricingService.payment_reminder_special(site_config, 9)
        fraction = PaymentService.proration_fraction(date(2026, 9, SEPTEMBER_CLASSES_START_DAY), 9, 2026)
        assert september["reduced_price_cheque_idioma"] == _euros(
            PaymentService.calculate_period_amount(enrollment, site_config, [9], fraction)
        )


class TestReminderFormUsesTheSpecialEmails:
    def _preview(self, client, month, **extra):
        response = client.post(
            reverse("payment_reminder_form"),
            {
                "action": "preview",
                "payment_start_date": "2026-09-01",
                "payment_end_date": "2026-09-05",
                "month": month,
                "iban_number": "ES1234",
                "telephone_number_bizum": "600000000",
                "reduced_price_cheque_idioma": "34",
                **extra,
            },
        )
        return response.json()["html"]

    def test_september_preview_explains_the_half_month(self, authenticated_client, site_config):
        html = self._preview(authenticated_client, "septiembre")
        assert "solo se cobra medio mes" in html
        assert f"del {SEPTEMBER_CLASSES_START_DAY} al 30" in html
        assert "50&nbsp;%" in html
        ctx = PricingService.payment_reminder_special(site_config, 9)
        assert f"{ctx['full_time_fee']} euros" in html

    @pytest.mark.parametrize("month", ["octubre", "septiembre", "junio", "abril"])
    def test_every_month_names_the_infantil_band_under_media_jornada(self, authenticated_client, site_config, month):
        """The reduced "infantil" band rides WITH the part-time row.

        It is the same one-session-a-week class at its own price, so it is a
        sub-line rather than a sixth row — but it has to appear in EVERY variant,
        including the two whose part-time row is adjusted (September prorates it,
        June discounts it). A month that quoted only the standard 36 € would send
        those families the wrong figure to transfer.
        """
        html = self._preview(authenticated_client, month)
        ctx = PricingService.payment_reminder_special(
            site_config, MESES_ES.index(month) + 1 if month in MESES_ES else None
        )
        assert "infantil" in html
        assert f"infantil: {ctx['part_time_child_fee']} euros" in html

    def test_september_and_june_carry_no_cheque_idioma_box(self, authenticated_client, site_config):
        """The cheque is not applied in the first (half) or last month of the course.

        Quoting a cheque-idioma figure for a month nobody is charged it is worse
        than silence: families read the reminder and transfer what it names.
        """
        for month in ("septiembre", "junio"):
            html = self._preview(authenticated_client, month)
            assert "Cheque Idioma" not in html, month
        # Every other month still carries it.
        assert "Cheque Idioma" in self._preview(authenticated_client, "octubre")

    def test_september_preview_follows_the_start_day(self, authenticated_client, site_config):
        html = self._preview(authenticated_client, "septiembre", september_start_day="15")
        assert "del 15 al 30" in html
        assert "53&nbsp;%" in html

    def test_june_preview_shows_the_discounted_fees(self, authenticated_client, site_config):
        html = self._preview(authenticated_client, "junio")
        ctx = PricingService.payment_reminder_special(site_config, 6)
        assert "último mes del curso" in html
        assert f"{ctx['full_time_fee']} euros" in html
        assert f"cuota habitual: {ctx['standard_full_time_fee']} euros" in html

    def test_april_preview_discounts_the_quarter(self, authenticated_client, site_config):
        html = self._preview(authenticated_client, "abril")
        ctx = PricingService.payment_reminder_special(site_config, 4)
        assert "abril, mayo y junio" in html
        assert f"{ctx['quarterly_fee']} euros" in html

    def test_a_regular_month_still_honours_the_typed_cheque_figure(self, authenticated_client, site_config):
        html = self._preview(authenticated_client, "octubre", reduced_price_cheque_idioma="33")
        assert "33 euros" in html
        assert "medio mes" not in html

    def test_mass_send_in_june_uses_the_june_email(self, authenticated_client, student_with_parent, site_config):
        response = authenticated_client.post(
            reverse("payment_reminder_form"),
            {
                "payment_start_date": "2027-06-01",
                "payment_end_date": "2027-06-05",
                "month": "junio",
                "iban_number": "ES1234567890",
                "telephone_number_bizum": "600000000",
            },
        )
        assert response.status_code == 302
        message = mail.outbox[0]
        assert "último mes" in message.subject
        body = message.alternatives[0][0]
        ctx = PricingService.payment_reminder_special(site_config, 6)
        assert "último mes del curso" in body
        assert f"{ctx['full_time_fee']} euros" in body

    def test_mass_send_in_september_uses_the_posted_start_day(
        self, authenticated_client, student_with_parent, site_config
    ):
        authenticated_client.post(
            reverse("payment_reminder_form"),
            {
                "payment_start_date": "2026-09-01",
                "payment_end_date": "2026-09-05",
                "month": "septiembre",
                "september_start_day": "15",
                "iban_number": "ES1234567890",
                "telephone_number_bizum": "600000000",
            },
        )
        message = mail.outbox[0]
        assert "(medio mes)" in message.subject
        assert "del 15 al 30" in message.alternatives[0][0]

    def test_form_page_carries_the_start_day_input(self, authenticated_client, site_config):
        page = authenticated_client.get(reverse("payment_reminder_form")).content.decode()
        assert 'name="september_start_day"' in page
        assert f'value="{SEPTEMBER_CLASSES_START_DAY}"' in page

    def test_test_all_emails_previews_the_three_variants(self, site_config):
        from comms.management.commands.test_all_emails import get_email_apps

        keys = {app["key"]: app for app in get_email_apps()}
        for case in ("september", "june", "april"):
            app = keys[f"payment_reminder_{case}"]
            assert app["template"] == f"payment_reminder_{case}"
            assert app["context"]["special_case"] == case
