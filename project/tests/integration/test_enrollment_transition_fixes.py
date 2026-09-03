"""v1.27 — enrollment transitions, payment write paths and the money helpers.

Every test here pins a bug that was reachable from the UI and invisible in the
data afterwards. The transition tests are driven through the SERVICE rather than
the endpoint wherever an exact date matters: `PaymentService.transition_start_date`
reads `date.today()` when nothing is passed, and a test that lets it do so is the
date bomb CLAUDE.md warns about twice. The explicit dates use the ELAPSED
2020-2021 course so every period has started and the counts hold on any run date.
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.contrib import admin as dj_admin
from django.urls import reverse

from billing.admin import PaymentAdmin
from billing.models import Enrollment, Payment, SiteConfiguration
from billing.services.enrollment_service import EnrollmentService
from billing.services.payment_service import PaymentService
from billing.services.pricing_service import (
    PricingService,
    quarterly_price_from_monthly,
    round_money,
)
from core.models import HistoryLog

pytestmark = pytest.mark.django_db

ELAPSED_YEAR = "2020-2021"


def _covered(payments) -> set[tuple[int, int]]:
    """The (month, year) pairs a set of periodic payments invoices.

    Spelled out here rather than reusing `PaymentService.covered_months` so the
    invariant under test ("the old plan's months and the new plan's months do not
    overlap") is asserted against an independent reading of the rows.
    """
    months: set[tuple[int, int]] = set()
    for payment in payments:
        month, year = payment.due_date.month, payment.due_date.year
        for _ in range(3 if payment.payment_type == "quarterly" else 1):
            months.add((month, year))
            month -= 1
            if month == 0:
                month, year = 12, year - 1
    return months


@pytest.fixture
def elapsed_monthly(student_with_parent, enrollment_type_new_student, site_config):
    """A monthly enrollment on a course that has fully elapsed."""
    return Enrollment.objects.create(
        student=student_with_parent,
        enrollment_type=enrollment_type_new_student,
        enrollment_period_start=date(2020, 9, 15),
        enrollment_period_end=date(2021, 6, 25),
        academic_year=ELAPSED_YEAR,
        schedule_type="full_time",
        payment_modality="monthly",
        enrollment_amount=Decimal("54.00"),
        discount_percentage=Decimal("0.00"),
        final_amount=Decimal("54.00"),
        status="active",
        enrollment_date=date(2020, 9, 1),
    )


def _monthly(enrollment, parent, month, day, status="completed"):
    return Payment.objects.create(
        student=enrollment.student,
        parent=parent,
        enrollment=enrollment,
        payment_type="monthly",
        payment_method="transfer",
        amount=Decimal("54.00"),
        payment_status=status,
        due_date=date(2020, month, day),
        payment_date=date(2020, month, day) if status == "completed" else None,
        concept=f"Mensualidad {month}/2020",
    )


# ============================================================================
# 1 — a modality change must never re-bill a month already invoiced
# ============================================================================


class TestModalityChangeCannotDoubleBill:
    def test_a_collected_month_is_never_reached_by_the_new_cadence(self, elapsed_monthly, student_with_parent, parent):
        """The headline bug: Sep + Oct collected monthly, flipped to quarterly in
        November, and the cron then invoiced a full-price Sep-Nov quarter."""
        _monthly(elapsed_monthly, parent, 9, 30)
        _monthly(elapsed_monthly, parent, 10, 31)
        _monthly(elapsed_monthly, parent, 11, 30, status="pending")

        start = EnrollmentService.supersede_enrollment(
            student_with_parent, elapsed_monthly, requested_start=date(2020, 11, 15), parent=parent
        )
        assert start == date(2020, 12, 1)

        replacement = EnrollmentService.replicate_enrollment(
            elapsed_monthly, start_date=start, payment_modality="quarterly"
        )
        PaymentService.schedule_academic_year_payments(replacement, parent, as_of=date(2021, 7, 1))

        old_months = _covered(Payment.objects.filter(enrollment=elapsed_monthly).exclude(payment_status="cancelled"))
        new_months = _covered(Payment.objects.filter(enrollment=replacement))

        assert {(9, 2020), (10, 2020), (11, 2020)} <= old_months
        assert not old_months & new_months, "a month may only be invoiced by one plan"
        assert (12, 2020) in new_months

        # The collected money is untouched: no cancel, no re-date.
        assert Payment.objects.filter(student=student_with_parent, payment_status="completed").count() == 2

    def test_a_collected_quarter_is_not_back_filled_month_by_month(
        self, student_with_parent, parent, enrollment_type_new_student, site_config
    ):
        """The mirror image: a paid Sep-Nov quarter, then a switch to monthly."""
        quarterly = Enrollment.objects.create(
            student=student_with_parent,
            enrollment_type=enrollment_type_new_student,
            enrollment_period_start=date(2020, 9, 15),
            enrollment_period_end=date(2021, 6, 25),
            academic_year=ELAPSED_YEAR,
            schedule_type="full_time",
            payment_modality="quarterly",
            enrollment_amount=Decimal("153.90"),
            discount_percentage=Decimal("0.00"),
            final_amount=Decimal("153.90"),
            status="active",
            enrollment_date=date(2020, 9, 1),
        )
        Payment.objects.create(
            student=student_with_parent,
            parent=parent,
            enrollment=quarterly,
            payment_type="quarterly",
            payment_method="transfer",
            amount=Decimal("153.90"),
            payment_status="completed",
            due_date=date(2020, 11, 30),
            payment_date=date(2020, 11, 30),
            concept="Trimestre Septiembre-Noviembre 2020",
        )

        start = EnrollmentService.supersede_enrollment(
            student_with_parent, quarterly, requested_start=date(2020, 11, 15), parent=parent
        )
        assert start == date(2020, 12, 1)

        replacement = EnrollmentService.replicate_enrollment(quarterly, start_date=start, payment_modality="monthly")
        PaymentService.schedule_academic_year_payments(replacement, parent, as_of=date(2021, 7, 1))

        billed = sorted(p.due_date for p in Payment.objects.filter(payment_type="monthly"))
        assert billed and billed[0] == date(2020, 12, 31), "the paid quarter's months must not be re-billed"

    def test_the_endpoint_supersedes_instead_of_mutating(
        self, authenticated_client, student_with_parent, active_enrollment, site_config
    ):
        response = authenticated_client.post(
            reverse("update_enrollment_modality", args=[student_with_parent.id]),
            data=json.dumps({"payment_modality": "quarterly"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        active_enrollment.refresh_from_db()
        assert active_enrollment.status == "finished"
        # The historical row keeps the cadence it actually billed under —
        # `enrollment_date` is the anchor every past payment was priced against.
        assert active_enrollment.payment_modality == "monthly"

        replacement = student_with_parent.enrollments.get(status="active")
        assert replacement.pk != active_enrollment.pk
        assert replacement.payment_modality == "quarterly"
        assert replacement.enrollment_date == date.fromisoformat(data["effective_start"])
        # A handover is always a month boundary, so no month is ever split.
        assert replacement.enrollment_date.day == 1
        assert data["enrollment_id"] == replacement.pk

    def test_a_hand_priced_enrollment_is_still_refused(
        self, authenticated_client, student_with_parent, enrollment_type_special, site_config
    ):
        Enrollment.objects.create(
            student=student_with_parent,
            enrollment_type=enrollment_type_special,
            enrollment_period_start=date(2020, 9, 15),
            enrollment_period_end=date(2021, 6, 25),
            academic_year=ELAPSED_YEAR,
            schedule_type="full_time",
            payment_modality="monthly",
            enrollment_amount=Decimal("25.00"),
            final_amount=Decimal("25.00"),
            status="active",
            enrollment_date=date(2020, 9, 1),
        )
        response = authenticated_client.post(
            reverse("update_enrollment_modality", args=[student_with_parent.id]),
            data=json.dumps({"payment_modality": "quarterly"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert student_with_parent.enrollments.filter(status="active").count() == 1


# ============================================================================
# 2 — the single canonical supersede, and the transition month
# ============================================================================


class TestSupersedeTransitionMonth:
    def test_a_mid_month_handover_moves_to_the_next_month_boundary(self, elapsed_monthly, student_with_parent):
        """The old plan taught 1-14 November, so November stays whole on it."""
        assert PaymentService.transition_start_date(
            student_with_parent, date(2020, 11, 15), closing=elapsed_monthly
        ) == date(2020, 12, 1)

    def test_a_first_of_the_month_handover_is_honoured_exactly(self, elapsed_monthly, student_with_parent):
        assert PaymentService.transition_start_date(
            student_with_parent, date(2020, 11, 1), closing=elapsed_monthly
        ) == date(2020, 11, 1)

    def test_a_first_enrollment_keeps_its_mid_month_proration(self, student_with_parent):
        """No predecessor means no month to split, so the date is left alone and
        `proration_fraction` still bills only the days remaining."""
        assert PaymentService.transition_start_date(student_with_parent, date(2020, 11, 15), closing=None) == date(
            2020, 11, 15
        )

    def test_the_closing_plan_bills_every_month_it_taught_and_nothing_after(
        self, elapsed_monthly, student_with_parent, parent
    ):
        """The gap the old view helper left: `generate_payments` only visits ACTIVE
        enrollments, so a month unbilled at handover can never be back-filled."""
        start = PaymentService.transition_start_date(student_with_parent, date(2020, 11, 15), closing=elapsed_monthly)
        created = PaymentService.close_out_periods(elapsed_monthly, parent=parent, until=start)

        dues = list(
            Payment.objects.filter(student=student_with_parent, payment_type="monthly")
            .order_by("due_date")
            .values_list("due_date", flat=True)
        )
        assert created == 3
        assert dues == [date(2020, 9, 30), date(2020, 10, 31), date(2020, 11, 30)]
        assert not Payment.objects.filter(student=student_with_parent, due_date__gte=start).exists()
        # Full months at the old price — the transition month is not prorated,
        # because the replacement does not bill any part of it.
        assert {p.amount for p in Payment.objects.filter(payment_type="monthly")} == {Decimal("54.00")}

    def test_closing_out_twice_creates_nothing_the_second_time(self, elapsed_monthly, student_with_parent, parent):
        start = date(2020, 12, 1)
        assert PaymentService.close_out_periods(elapsed_monthly, parent=parent, until=start) == 3
        assert PaymentService.close_out_periods(elapsed_monthly, parent=parent, until=start) == 0

    def test_a_refused_transition_writes_nothing(self, elapsed_monthly, student_with_parent, parent):
        """Every remaining month is invoiced, so the change cannot take effect —
        and the enrollment must be left exactly as it was."""
        PaymentService.schedule_academic_year_payments(elapsed_monthly, parent, as_of=date(2021, 7, 1))
        payments_before = set(Payment.objects.values_list("id", flat=True))

        assert (
            PaymentService.transition_start_date(student_with_parent, date(2021, 3, 15), closing=elapsed_monthly)
            is None
        )
        assert (
            EnrollmentService.supersede_enrollment(
                student_with_parent, elapsed_monthly, requested_start=date(2021, 3, 15), parent=parent
            )
            is None
        )

        elapsed_monthly.refresh_from_db()
        assert elapsed_monthly.status == "active"
        assert set(Payment.objects.values_list("id", flat=True)) == payments_before

    def test_a_cancelled_row_frees_its_month_again(self, elapsed_monthly, student_with_parent, parent):
        """Cancelling frees the month under the DB constraint, so it must free it
        here too — otherwise a superseded row reserves a month forever."""
        _monthly(elapsed_monthly, parent, 11, 30, status="pending")
        assert (11, 2020) in PaymentService.covered_months(student_with_parent)

        Payment.objects.filter(due_date=date(2020, 11, 30)).update(payment_status="cancelled")
        assert (11, 2020) not in PaymentService.covered_months(student_with_parent)


# ============================================================================
# 3 — a refunded payment must not be resurrected
# ============================================================================


class TestRefundedPaymentCannotBeCompleted:
    def _url(self, payment):
        return reverse("quick_complete_payment", kwargs={"payment_id": payment.id})

    @pytest.mark.parametrize("dead_status", ["cancelled", "refunded"])
    def test_quick_complete_refuses_dead_money(self, authenticated_client, pending_payment, dead_status):
        pending_payment.payment_status = dead_status
        pending_payment.save()

        response = authenticated_client.post(
            self._url(pending_payment),
            data=json.dumps({"payment_method": "cash"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["success"] is False

        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == dead_status
        assert pending_payment.payment_date is None

    def test_the_refusal_comes_from_the_model_guard(self, pending_payment):
        """The view must not carry its own copy of the status list — that copy is
        what was missing `refunded`."""
        from django.core.exceptions import ValidationError

        pending_payment.payment_status = "refunded"
        with pytest.raises(ValidationError):
            pending_payment.assert_completable(previous_status="refunded")
        # A live payment passes.
        pending_payment.assert_completable(previous_status="pending")


# ============================================================================
# 4 — the admin must not void collected money
# ============================================================================


class _CapturingPaymentAdmin(PaymentAdmin):
    def __init__(self):
        super().__init__(Payment, dj_admin.site)
        self.captured = []

    def message_user(self, request, message, *args, **kwargs):
        self.captured.append(message)


class TestBulkStatusGuard:
    @pytest.mark.parametrize("action", ["mark_as_failed", "soft_delete_payments"])
    def test_a_completed_payment_is_never_voided(self, completed_payment, action):
        payment_admin = _CapturingPaymentAdmin()
        getattr(payment_admin, action)(None, Payment.objects.filter(pk=completed_payment.pk))

        completed_payment.refresh_from_db()
        assert completed_payment.payment_status == "completed"
        assert completed_payment.payment_date is not None
        assert "sin tocar" in payment_admin.captured[0]

    @pytest.mark.parametrize(
        ("action", "expected"),
        [("mark_as_failed", "failed"), ("soft_delete_payments", "cancelled")],
    )
    def test_a_pending_payment_still_moves(self, pending_payment, action, expected):
        payment_admin = _CapturingPaymentAdmin()
        getattr(payment_admin, action)(None, Payment.objects.filter(pk=pending_payment.pk))

        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == expected
        assert payment_admin.captured[0].startswith("1 pagos marcados como")


# ============================================================================
# 6 — transactions and the on-commit receipt
# ============================================================================


class TestPaymentWritesAreAtomic:
    def test_a_failed_history_entry_rolls_the_payment_back(
        self, authenticated_client, student_with_parent, parent, active_enrollment, monkeypatch
    ):
        def _boom(*args, **kwargs):
            raise RuntimeError("history is down")

        monkeypatch.setattr(HistoryLog, "log", _boom)
        before = Payment.objects.count()

        response = authenticated_client.post(
            reverse("create_payment"),
            {
                "student_id": student_with_parent.id,
                "parent_id": parent.id,
                "payment_type": "other",
                "payment_method": "transfer",
                "amount": "54.00",
                "currency": "EUR",
                "payment_status": "pending",
                "due_date": "2026-05-01",
                "concept": "Pago que no debe quedar",
            },
        )
        assert response.status_code == 302
        # The user is told it failed, so the payment must not exist — otherwise
        # the obvious retry creates a second one.
        assert Payment.objects.count() == before
        assert not Payment.objects.filter(concept="Pago que no debe quedar").exists()

    def test_the_receipt_is_dispatched_on_commit(
        self, authenticated_client, pending_payment, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            response = authenticated_client.post(
                reverse("quick_complete_payment", kwargs={"payment_id": pending_payment.id}),
                data=json.dumps({"payment_method": "cash"}),
                content_type="application/json",
            )
        assert response.status_code == 200
        assert len(callbacks) == 1, "the receipt must wait for COMMIT, not fire inside the transaction"


# ============================================================================
# 7 — the CSV export answers the same question as the page
# ============================================================================


class TestExportHonoursFilters:
    def test_month_and_year_narrow_the_file(self, authenticated_client, pending_payment, completed_payment):
        body = authenticated_client.get(reverse("export_payments"), {"year": 2025, "month": 10}).content.decode("utf-8")
        assert pending_payment.concept in body
        assert completed_payment.concept not in body

    def test_search_narrows_the_file(self, authenticated_client, pending_payment, completed_payment):
        body = authenticated_client.get(
            reverse("export_payments"), {"search": completed_payment.concept}
        ).content.decode("utf-8")
        assert completed_payment.concept in body
        assert pending_payment.concept not in body

    def test_no_parameters_still_exports_everything(self, authenticated_client, pending_payment, completed_payment):
        """The export link is a plain anchor, so "no filters" must not be read as
        "this calendar year" — that would silently drop rows nobody excluded."""
        body = authenticated_client.get(reverse("export_payments")).content.decode("utf-8")
        assert pending_payment.concept in body
        assert completed_payment.concept in body


# ============================================================================
# 9 — the academy's fiscal details are real fields now
# ============================================================================


class TestAcademyFiscalDetails:
    def test_they_are_editable_from_management(self, authenticated_client, site_config):
        response = authenticated_client.post(
            reverse("update_site_config"),
            data=json.dumps({"academy_cif": "B02123456", "academy_name": "Five a Day SL"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        config = SiteConfiguration.get_config(refresh=True)
        assert config.academy_cif == "B02123456"
        assert config.academy_name == "Five a Day SL"

    def test_the_tax_certificate_header_picks_up_the_cif(self, site_config):
        """`pdf_service._get_academy_info()` read these five names off the config
        all along; they simply did not exist, so the CIF printed blank on a
        document that asserts IRPF validity."""
        from billing.services.pdf_service import _get_academy_info

        site_config.academy_cif = "B02123456"
        site_config.save()
        assert _get_academy_info().cif == "B02123456"

    def test_a_price_edit_still_works_with_the_new_fields_blank(self, authenticated_client, site_config):
        """They are `blank=True` on purpose: `update_site_config` runs
        `full_clean()`, so a non-blank field would 400 every price edit."""
        SiteConfiguration.objects.filter(pk=1).update(academy_cif="", academy_name="", academy_address="")
        response = authenticated_client.post(
            reverse("update_site_config"),
            data=json.dumps({"full_time_monthly_fee": "58.00"}),
            content_type="application/json",
        )
        assert response.status_code == 200


# ============================================================================
# 10 + 11 — one predicate, one rounding, one quarterly formula
# ============================================================================


class TestSharedPricingHelpers:
    def test_is_hand_priced(self, active_enrollment, enrollment_type_special):
        assert active_enrollment.is_hand_priced is False
        active_enrollment.enrollment_type = enrollment_type_special
        assert active_enrollment.is_hand_priced is True

    def test_is_hand_priced_on_a_bare_instance(self):
        """`enrollment_type` is a non-nullable FK, so a naive attribute read
        raises `RelatedObjectDoesNotExist` on an unsaved carrier row."""
        assert Enrollment().is_hand_priced is False

    def test_it_agrees_with_the_billing_short_circuit(self, active_enrollment, enrollment_type_special):
        active_enrollment.enrollment_type = enrollment_type_special
        assert active_enrollment.is_hand_priced is True
        assert PaymentService.hand_priced_amount(active_enrollment) is not None

    def test_the_quarterly_save_fallback_matches_the_advertised_price(
        self, student, enrollment_type_new_student, site_config
    ):
        enrollment = Enrollment(
            student=student,
            enrollment_type=enrollment_type_new_student,
            enrollment_period_start=date(2020, 9, 15),
            enrollment_period_end=date(2021, 6, 25),
            academic_year=ELAPSED_YEAR,
            schedule_type="full_time",
            payment_modality="quarterly",
            discount_percentage=Decimal("0.00"),
            status="active",
            enrollment_date=date(2020, 9, 1),
        )
        enrollment.save()
        assert enrollment.final_amount == round_money(PricingService.calculate_quarterly_price(site_config))
        assert enrollment.enrollment_amount == enrollment.final_amount

    def test_the_save_fallback_applies_the_quarter_formula_to_the_selected_base(
        self, student, enrollment_type_new_student, site_config
    ):
        """The constraint that forced the shared helper to be parameterised:
        `calculate_quarterly_price` is always full-time, but the fallback must use
        whatever base `schedule_type` selected."""
        enrollment = Enrollment(
            student=student,
            enrollment_type=enrollment_type_new_student,
            enrollment_period_start=date(2020, 9, 15),
            enrollment_period_end=date(2021, 6, 25),
            academic_year=ELAPSED_YEAR,
            schedule_type="part_time",
            payment_modality="quarterly",
            discount_percentage=Decimal("0.00"),
            status="active",
            enrollment_date=date(2020, 9, 1),
        )
        enrollment.save()
        expected = round_money(quarterly_price_from_monthly(site_config.part_time_monthly_fee, site_config))
        assert enrollment.final_amount == expected
        assert expected != round_money(PricingService.calculate_quarterly_price(site_config))

    def test_rounding_floors_at_one_cent(self):
        assert round_money(Decimal("-5.00")) == Decimal("0.01")
        assert round_money(Decimal("0.00")) == Decimal("0.01")
        # HALF_UP, not HALF_EVEN: 146.205 must bill 146.21, as the invoice does.
        assert round_money(Decimal("146.205")) == Decimal("146.21")


# ============================================================================
# 12 — a capped picker must say so
# ============================================================================


class TestPickerTruncationIsAnnounced:
    def test_the_sibling_picker_announces_its_cap(self, authenticated_client, student, group, site_config, monkeypatch):
        """A sibling past the cap was simply unfindable, so the admin left
        "Descuento hermano" unticked and the family lost the discount all year —
        a mis-bill with nothing on screen to explain it."""
        from students.models import Student

        Student.objects.create(first_name="Hermana", last_name="Segunda", group=group, active=True)
        monkeypatch.setattr("core.views.students._PICKER_CAP", 1)

        response = authenticated_client.get(reverse("student_create"))
        assert len(response.context["all_students_for_sibling"]) == 1
        assert "Mostrando solo los primeros 1 de 2" in response.context["sibling_list_notice"]
        assert response.context["sibling_search_url"] == reverse("search_students")

    def test_no_notice_when_nothing_was_dropped(self, authenticated_client, student, site_config):
        response = authenticated_client.get(reverse("student_create"))
        assert response.context["sibling_list_notice"] == ""

    def test_the_existing_parent_picker_is_bounded(self, authenticated_client, parent, site_config):
        response = authenticated_client.get(reverse("student_create") + "?mode=existing_parent")
        assert list(response.context["all_parents"]) == [parent]
        assert response.context["all_parents_notice"] == ""
