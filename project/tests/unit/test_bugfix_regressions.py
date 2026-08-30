"""Regression tests for the v1.15 bug-fix pass.

Every test here pins a behaviour that was *verified broken* against the running
app before it was fixed. They are grouped by the defect they guard, and each
docstring records what actually went wrong — the point is that a future
refactor that reintroduces the bug fails here rather than in production.

The pre-existing suite passed at 95%+ line coverage while every one of these
bugs was live, which is the reason they assert on behaviour (what the user
sees, what lands in the DB, what gets emailed) rather than on lines executed.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.test import Client

from billing.models import Enrollment, Payment

pytestmark = pytest.mark.django_db


def _client(**session_extra):
    """Session-authenticated client that tolerates 500s (so we can assert on them)."""
    c = Client(raise_request_exception=False)
    session = c.session
    session["is_authenticated"] = True
    session["username"] = "tester"
    for key, value in session_extra.items():
        session[key] = value
    session.save()
    return c


# ─────────────────────────────────────────────────────────────────────────────
# Adult students have no parent — Payment.parent is nullable
# ─────────────────────────────────────────────────────────────────────────────


class TestAdultStudentPaymentsDoNotCrash:
    """`payment.parent.full_name` with no None guard 500'd two live endpoints.

    A single adult-student payment was enough to break payment search and the
    whole CSV export. `get_payment_details` already had the guard, so the same
    fix had been applied in one of three places.
    """

    @pytest.fixture
    def adult_payment(self, adult_student):
        return Payment.objects.create(
            student=adult_student,
            parent=None,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("60.00"),
            payment_status="pending",
            due_date=date(2026, 1, 31),
            concept="Mensualidad adulto",
        )

    def test_search_payments_handles_missing_parent(self, adult_payment, adult_student):
        response = _client().get(f"/api/search/payments/?q={adult_student.last_name}")
        assert response.status_code == 200
        results = response.json()["results"]
        assert results, "the adult student's payment should still be findable"
        assert results[0]["parent_name"] == ""

    def test_csv_export_handles_missing_parent(self, adult_payment):
        response = _client().get("/payments/export/")
        assert response.status_code == 200
        assert "Mensualidad adulto" in response.content.decode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Quarterly billing
# ─────────────────────────────────────────────────────────────────────────────


class TestQuarterlyAmountAppliesDiscounts:
    """Reported as "es la misma cantidad con y sin descuento".

    `calculate_quarterly_amount` applied only the quarterly percentage, so a
    quarterly student with a sibling discount or a language cheque was billed
    the full price — and the Enrollment row said something different from the
    Payments generated for it.
    """

    def _enrollment(self, student, et, *, sibling=False, cheque=False):
        return Enrollment.objects.create(
            student=student,
            enrollment_type=et,
            enrollment_period_start=date(2025, 9, 15),
            enrollment_period_end=date(2026, 6, 27),
            academic_year="2025-2026",
            schedule_type="full_time",
            payment_modality="quarterly",
            is_sibling_discount=sibling,
            has_language_cheque=cheque,
            enrollment_amount=Decimal("153.90"),
            final_amount=Decimal("153.90"),
            status="active",
            enrollment_date=date(2025, 9, 1),
        )

    def test_plain_quarterly_is_three_months_minus_the_quarterly_percentage(
        self, student, enrollment_type_returning_student, site_config
    ):
        from billing.services.payment_service import PaymentService

        e = self._enrollment(student, enrollment_type_returning_student)
        expected = site_config.full_time_monthly_fee * 3
        expected -= expected * (site_config.quarterly_enrollment_discount / Decimal("100"))
        assert PaymentService.calculate_quarterly_amount(e, site_config, 10) == expected

    def test_sibling_discount_lowers_the_quarterly_amount(
        self, student, enrollment_type_returning_student, site_config
    ):
        from billing.services.payment_service import PaymentService

        plain = PaymentService.calculate_quarterly_amount(
            self._enrollment(student, enrollment_type_returning_student), site_config, 10
        )
        Enrollment.objects.all().delete()
        with_sibling = PaymentService.calculate_quarterly_amount(
            self._enrollment(student, enrollment_type_returning_student, sibling=True), site_config, 10
        )
        assert with_sibling < plain, "sibling discount must change the quarterly amount"

    def test_language_cheque_is_applied_three_times(self, student, enrollment_type_returning_student, site_config):
        """A quarter covers three months, so it carries three cheques."""
        from billing.services.payment_service import PaymentService

        plain = PaymentService.calculate_quarterly_amount(
            self._enrollment(student, enrollment_type_returning_student), site_config, 10
        )
        Enrollment.objects.all().delete()
        with_cheque = PaymentService.calculate_quarterly_amount(
            self._enrollment(student, enrollment_type_returning_student, cheque=True), site_config, 10
        )
        assert plain - with_cheque == site_config.language_cheque_discount * 3

    def test_q3_carries_the_june_discount(self, student, enrollment_type_returning_student, site_config):
        """Q3 (due April) covers April-June, so it picks up the June discount
        that calculate_monthly_amount applies to month 6. `quarter_due_month`
        was an unused parameter before this."""
        from billing.services.payment_service import PaymentService

        e = self._enrollment(student, enrollment_type_returning_student)
        q1 = PaymentService.calculate_quarterly_amount(e, site_config, 10)
        q3 = PaymentService.calculate_quarterly_amount(e, site_config, 4)
        assert q1 - q3 == site_config.june_discount

    def test_adult_group_keeps_the_flat_rate(self, student, enrollment_type_returning_student, site_config):
        """Adults pay a flat rate — no sibling/cheque/June discounts, matching
        calculate_monthly_amount's early return."""
        from billing.services.payment_service import PaymentService

        e = self._enrollment(student, enrollment_type_returning_student, sibling=True, cheque=True)
        e.schedule_type = "adult_group"
        e.save()
        expected = site_config.adult_group_monthly_fee * 3
        expected -= expected * (site_config.quarterly_enrollment_discount / Decimal("100"))
        assert PaymentService.calculate_quarterly_amount(e, site_config, 10) == expected


# ─────────────────────────────────────────────────────────────────────────────
# Payment lifecycle
# ─────────────────────────────────────────────────────────────────────────────


class TestCompletedPaymentAlwaysHasADate:
    """A completed payment with payment_date=None is invisible to every income
    figure — they all filter on payment_date. `update_payment` called save()
    without full_clean(), so Payment.clean()'s date backfill never ran and €54
    of real income reported as €0.
    """

    def test_marking_completed_backfills_the_payment_date(self, student_with_parent, parent, active_enrollment):
        import json

        from billing.services.expense_service import monthly_totals

        payment = Payment.objects.create(
            student=student_with_parent,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("54.00"),
            payment_status="pending",
            due_date=date.today(),
            concept="probe",
        )
        response = _client().post(
            f"/payments/{payment.id}/update/",
            data=json.dumps({"payment_status": "completed"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        payment.refresh_from_db()
        assert payment.payment_date is not None

        totals = monthly_totals(date.today().month, date.today().year)
        assert totals["income"] == Decimal("54.00"), "the money must show up as income"


class TestQuickCompleteIsIdempotent:
    """Re-completing an already-completed payment rewrote its historical
    payment_date to today, silently moving the money into a different month in
    every report. The Stripe webhook had this guard; the one-click button did not.
    """

    def test_second_completion_does_not_rewrite_the_date(self, student_with_parent, parent, active_enrollment):
        import json

        original = date(2025, 9, 5)
        payment = Payment.objects.create(
            student=student_with_parent,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="cash",
            amount=Decimal("54.00"),
            payment_status="completed",
            payment_date=original,
            due_date=date(2025, 9, 30),
            concept="probe",
        )
        response = _client().post(
            f"/api/payments/{payment.id}/quick-complete/",
            data=json.dumps({"payment_method": "transfer"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json().get("already_completed") is True
        payment.refresh_from_db()
        assert payment.payment_date == original


class TestPaymentAttachesToTheActiveEnrollment:
    """`student.enrollments.first()` has no ordering and no status filter, so a
    returning student's new payment attached to their OLD finished enrollment.
    """

    def test_prefers_active_over_finished(self, student_with_parent, parent, enrollment_type_new_student, site_config):
        common = {
            "student": student_with_parent,
            "enrollment_type": enrollment_type_new_student,
            "schedule_type": "full_time",
            "payment_modality": "monthly",
            "enrollment_amount": Decimal("54.00"),
            "final_amount": Decimal("54.00"),
        }
        # Created FIRST, so it is the one an unordered .first() returns.
        Enrollment.objects.create(
            enrollment_period_start=date(2024, 9, 15),
            enrollment_period_end=date(2025, 6, 27),
            academic_year="2024-2025",
            status="finished",
            enrollment_date=date(2024, 9, 1),
            **common,
        )
        current = Enrollment.objects.create(
            enrollment_period_start=date(2025, 9, 15),
            enrollment_period_end=date(2026, 6, 27),
            academic_year="2025-2026",
            status="active",
            enrollment_date=date(2025, 9, 1),
            **common,
        )

        _client().post(
            "/payments/create/",
            data={
                "student_id": student_with_parent.id,
                "parent_id": parent.id,
                "payment_type": "monthly",
                "payment_method": "transfer",
                "amount": "54.00",
                "payment_status": "pending",
                "due_date": "2026-03-31",
                "concept": "probe-active-enrollment",
            },
        )
        payment = Payment.objects.get(concept="probe-active-enrollment")
        assert payment.enrollment_id == current.id


class TestPaymentChoiceValidation:
    """Model.objects.create() does not enforce `choices`, so raw POST values
    reached the DB and get_..._display() echoed them straight back.
    """

    def test_invalid_choices_fall_back_to_defaults(self, student_with_parent, parent):
        from billing import constants

        _client().post(
            "/payments/create/",
            data={
                "student_id": student_with_parent.id,
                "parent_id": parent.id,
                "payment_type": "bogus_type",
                "payment_method": "bitcoin",
                "amount": "54.00",
                "payment_status": "wat",
                "due_date": "2026-03-31",
                "concept": "probe-choices",
            },
        )
        payment = Payment.objects.get(concept="probe-choices")
        assert payment.payment_type in dict(constants.PAYMENT_TYPE_CHOICES)
        assert payment.payment_method in dict(constants.PAYMENT_METHOD_CHOICES)
        assert payment.payment_status in dict(constants.PAYMENT_STATUS_CHOICES)


class TestCreatePaymentDoesNotLeakInternals:
    """`messages.error(f"...{str(e)}")` echoed decimal.ConversionSyntax and
    Postgres column widths to the browser — the exact leak class v1.14.4/5
    cleared 46 CodeQL alerts for.
    """

    @pytest.mark.parametrize(
        ("field", "value", "leak"),
        [
            ("amount", "not-a-number", "ConversionSyntax"),
            ("concept", "x" * 400, "character varying"),
        ],
    )
    def test_error_message_is_fixed_text(self, student_with_parent, parent, field, value, leak):
        payload = {
            "student_id": student_with_parent.id,
            "parent_id": parent.id,
            "payment_type": "monthly",
            "payment_method": "transfer",
            "amount": "54.00",
            "payment_status": "pending",
            "due_date": "2026-03-31",
            "concept": "probe-leak",
        }
        payload[field] = value
        response = _client().post("/payments/create/", data=payload, follow=True)
        shown = " ".join(str(m.message) for m in response.context["messages"])
        assert leak not in shown, f"internal detail leaked to the user: {shown}"


# ─────────────────────────────────────────────────────────────────────────────
# "Esperado" accounting
# ─────────────────────────────────────────────────────────────────────────────


class TestCancelledPaymentsAreNotExpectedRevenue:
    """Reported as "no entiendo el apartado de esperado".

    Cancelling a duplicate payment left it counted as money still expected, so
    the collection rate collapsed toward 0% and three views disagreed about the
    same number.
    """

    @pytest.fixture
    def cancelled(self, student_with_parent, parent, active_enrollment):
        payment = Payment.objects.create(
            student=student_with_parent,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("100.00"),
            payment_status="pending",
            due_date=date.today(),
            concept="duplicado",
        )
        _client().post(f"/payments/{payment.id}/deactivate/")
        payment.refresh_from_db()
        assert payment.payment_status == "cancelled"
        return payment

    def test_collection_rate_excludes_it(self, cancelled):
        from core.services.analytics_service import collection_rate

        today = date.today()
        assert collection_rate(today.month, today.year)["expected"] == Decimal("0.00")

    def test_dashboard_expected_revenue_excludes_it(self, cancelled):
        assert _client().get("/").context["expected_revenue"] == Decimal("0.00")

    def test_payments_list_expected_total_excludes_it(self, cancelled):
        context = _client().get("/payments/").context
        assert context["expected_payments_total"] == Decimal("0.00")

    def test_all_three_views_agree(self, cancelled):
        """The whole point: one definition of "esperado", not three."""
        from core.services.analytics_service import collection_rate

        today = date.today()
        assert (
            collection_rate(today.month, today.year)["expected"]
            == _client().get("/").context["expected_revenue"]
            == _client().get("/payments/").context["expected_payments_total"]
        )


# ─────────────────────────────────────────────────────────────────────────────
# Hardening: inputs that used to 500
# ─────────────────────────────────────────────────────────────────────────────


class TestHandEditedQueryStringsDoNotCrash:
    """Each of these produced an unhandled 500 from an ordinary URL."""

    @pytest.mark.parametrize(
        "url",
        [
            "/api/history/?offset=-1",  # ValueError: negative slicing
            "/reports/?year=-1",  # ValueError: year -1 is out of range
            "/reports/?year=999999999999",  # OverflowError
            "/reports/?year=abc",
            "/reports/download.pdf?year=-1",
            "/students/create/?parent_id=abc",  # int() on a non-numeric id
            "/expenses/?month=13",
            "/payments/?month=99&year=abc",
        ],
    )
    def test_no_server_error(self, url, site_config):
        assert _client().get(url).status_code < 500

    def test_over_long_todo_is_rejected_not_crashed(self):
        import json

        response = _client().post(
            "/api/todos/create/",
            data=json.dumps({"text": "x" * 600, "due_date": "2026-12-31"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["success"] is False


class TestSiteConfigRejectsInvalidPrices:
    """`config.save()` skips validators, so a negative fee persisted straight
    through and quietly broke every downstream price calculation.
    """

    def test_negative_fee_is_rejected(self, site_config):
        import json

        from billing.models import SiteConfiguration

        response = _client().post(
            "/api/config/update/",
            data=json.dumps({"full_time_monthly_fee": "-50.00"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert SiteConfiguration.get_config().full_time_monthly_fee > 0

    def test_valid_change_still_works(self, site_config):
        import json

        from billing.models import SiteConfiguration

        response = _client().post(
            "/api/config/update/",
            data=json.dumps({"full_time_monthly_fee": "58.00"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert SiteConfiguration.get_config().full_time_monthly_fee == Decimal("58.00")


class TestSingletonsResistDeletion:
    """`delete()` returned None (breaking Django's (count, dict) contract) and
    `objects.all().delete()` bypassed the guard entirely, wiping every price.
    """

    def test_instance_delete_returns_djangos_tuple(self, site_config):
        from billing.models import SiteConfiguration

        # Call outside the assert: under `python -O` asserts are stripped, and
        # with them the delete() that this test exists to exercise.
        result = site_config.delete()

        assert result == (0, {})
        assert SiteConfiguration.objects.count() == 1

    def test_queryset_delete_is_blocked(self, site_config):
        from billing.models import SiteConfiguration

        SiteConfiguration.objects.all().delete()
        assert SiteConfiguration.objects.count() == 1


class TestEnrollmentAmountFallback:
    """The `if not self.enrollment_amount` fallback was nested inside
    `if not self.final_amount`, so supplying one but not the other skipped it
    and the insert died on a NOT NULL violation.
    """

    def test_final_amount_without_enrollment_amount_saves(self, student, enrollment_type_new_student):
        enrollment = Enrollment(
            student=student,
            enrollment_type=enrollment_type_new_student,
            enrollment_period_start=date(2025, 9, 15),
            enrollment_period_end=date(2026, 6, 27),
            academic_year="2025-2026",
            schedule_type="full_time",
            final_amount=Decimal("54.00"),
            status="pending",
            enrollment_date=date(2025, 9, 1),
        )
        enrollment.save()
        assert enrollment.enrollment_amount == Decimal("54.00")
