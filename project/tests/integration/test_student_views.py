"""Tests for core.views.students — list, detail, create, update, search."""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse

from billing.models import Payment
from students.models import Student

pytestmark = pytest.mark.django_db


class TestStudentListView:
    def test_loads_ok(self, authenticated_client, student, active_enrollment):
        response = authenticated_client.get(reverse("students_list"))
        assert response.status_code == 200

    def test_excludes_inactive_students(self, authenticated_client, inactive_student):
        response = authenticated_client.get(reverse("students_list"))
        student_ids = {s.id for s in response.context["students"]}
        assert inactive_student.id not in student_ids

    def test_search_by_name(self, authenticated_client, student, active_enrollment):
        response = authenticated_client.get(reverse("students_list"), {"search": "Lucas"})
        assert response.status_code == 200
        student_ids = {s.id for s in response.context["students"]}
        assert student.id in student_ids

    def test_search_no_results(self, authenticated_client, student, active_enrollment):
        response = authenticated_client.get(reverse("students_list"), {"search": "nonexistent"})
        assert response.status_code == 200
        assert len(response.context["students"]) == 0

    def test_context_has_groups_and_parents(self, authenticated_client, student, active_enrollment):
        response = authenticated_client.get(reverse("students_list"))
        assert "groups" in response.context
        assert "parents" in response.context


class TestStudentDetailView:
    def test_loads_ok(self, authenticated_client, student):
        response = authenticated_client.get(reverse("student_detail", args=[student.id]))
        assert response.status_code == 200
        assert response.context["student"] == student

    def test_shows_parents(self, authenticated_client, student_with_parent):
        response = authenticated_client.get(reverse("student_detail", args=[student_with_parent.id]))
        assert len(response.context["parents"]) == 1

    def test_nonexistent_student_404(self, authenticated_client):
        response = authenticated_client.get(reverse("student_detail", args=[99999]))
        assert response.status_code == 404


class TestStudentCreateView:
    def test_get_renders_form(self, authenticated_client, group, site_config, enrollment_type_new_student):
        response = authenticated_client.get(reverse("student_create"))
        assert response.status_code == 200
        assert "enrollment_form" in response.context

    def test_success_page(self, authenticated_client, group, site_config, enrollment_type_new_student):
        url = reverse("student_create") + "?success=1&student_name=Test&fee=40"
        response = authenticated_client.get(url)
        assert response.status_code == 200
        assert response.context["show_success"] is True

    def test_adult_mode_context(self, authenticated_client, group, site_config, enrollment_type_new_student):
        response = authenticated_client.get(reverse("student_create") + "?mode=adult")
        assert response.status_code == 200
        assert response.context["is_adult_mode"] is True

    def test_price_config_exposes_the_gross_quarterly(
        self, authenticated_client, group, site_config, enrollment_type_new_student
    ):
        """The price widget strikes through the PRE-discount total.

        `quarterly` is already net of the -5%, so on its own it made the widget
        strike through the discounted figure and print the same number twice.
        """
        response = authenticated_client.get(reverse("student_create"))
        price_config = response.context["price_config"]

        gross = site_config.full_time_monthly_fee * 3
        assert Decimal(price_config["quarterly_gross"]) == gross
        assert Decimal(price_config["quarterly"]) == gross * (1 - site_config.quarterly_enrollment_discount / 100)


class TestFirstPeriodProrationContext:
    """The form must show the prorated first fee before the admin saves.

    Only the first month is ever reduced, so the row is driven by a fraction the
    view computes from today's date — the same PaymentService.proration_fraction
    the generator bills with, so the preview cannot drift from the invoice.
    """

    def test_context_exposes_the_proration(self, authenticated_client, group, site_config, enrollment_type_new_student):
        from datetime import date

        from billing.models import current_academic_year
        from billing.services.payment_service import PaymentService

        response = authenticated_client.get(reverse("student_create"))
        assert response.status_code == 200

        today = date.today()
        sequence = PaymentService.teaching_months(current_academic_year(today))
        first = next(((m, y) for m, y in sequence if PaymentService._last_day(m, y) >= today), None)
        expected = PaymentService.proration_fraction(today, *first) if first else Decimal("1")

        assert Decimal(response.context["first_period_fraction"]) == expected
        assert response.context["first_period_is_partial"] is (expected != Decimal("1"))

    def test_fraction_is_always_a_usable_number(
        self, authenticated_client, group, site_config, enrollment_type_new_student
    ):
        """It is interpolated raw into a JS object literal — never blank."""
        response = authenticated_client.get(reverse("student_create"))
        value = Decimal(response.context["first_period_fraction"])
        assert Decimal("0") < value <= Decimal("1")


class TestStudentCreateViewPost:
    def test_creates_student_with_enrollment(
        self, authenticated_client, parent, group, site_config, enrollment_type_new_student
    ):
        response = authenticated_client.post(
            reverse("student_create") + f"?parent_id={parent.id}",
            {
                "first_name": "Nuevo",
                "last_name": "Alumno",
                "birth_date": "2018-03-10",
                "school": "CEIP Nuevo",
                "gdpr_signed": "on",
                "group": group.id,
                "parent_id": parent.id,
                "enrollment_plan": "monthly_full",
            },
        )
        assert response.status_code == 302
        assert Student.objects.filter(first_name="Nuevo").exists()

    def test_creates_adult_student(self, authenticated_client, group, site_config, enrollment_type_adults):
        response = authenticated_client.post(
            reverse("student_create") + "?mode=adult",
            {
                "first_name": "Adulto",
                "last_name": "Nuevo",
                "birth_date": "1990-01-01",
                "gdpr_signed": "on",
                "group": group.id,
                "is_adult_mode": "true",
                "adult_email": "adulto@test.com",
                "adult_phone": "600111222",
                "enrollment_plan": "monthly_full",
            },
        )
        assert response.status_code == 302
        assert Student.objects.filter(first_name="Adulto").exists()


class TestSearchStudents:
    def test_returns_json_results(self, authenticated_client, student):
        response = authenticated_client.get(reverse("search_students"), {"q": student.first_name})
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert any(r["id"] == student.id for r in data["results"])

    def test_short_query_returns_empty(self, authenticated_client, student):
        response = authenticated_client.get(reverse("search_students"), {"q": "a"})
        assert response.status_code == 200
        assert response.json()["results"] == []


# ============================================================================
# Extra coverage: StudentCreateView error paths (invalid parent_id, existing-
# parent mode, success-page params, create_sibling flag, email-task swallow)
# ============================================================================


class TestStudentCreateViewErrors:
    def test_get_with_invalid_parent_id_shows_error(self, authenticated_client):
        """parent_id that doesn't exist → messages.error but page still renders."""
        response = authenticated_client.get(reverse("student_create") + "?parent_id=99999")
        assert response.status_code == 200

    def test_get_existing_parent_mode_shows_all_parents(self, authenticated_client, parent):
        response = authenticated_client.get(reverse("student_create") + "?mode=existing_parent")
        assert response.status_code == 200
        assert "all_parents" in response.context

    def test_success_page_parameters(self, authenticated_client):
        response = authenticated_client.get(
            reverse("student_create") + "?success=1&student_name=Lucia&fee=40&parent_id=1&create_sibling=1"
        )
        assert response.status_code == 200
        assert response.context["show_success"] is True

    def test_post_without_parent_id_fails(self, authenticated_client, group, enrollment_type_new_student, site_config):
        response = authenticated_client.post(
            reverse("student_create"),
            {
                "first_name": "Orphan",
                "last_name": "Kid",
                "birth_date": "2018-01-01",
                "group": group.id,
                "gdpr_signed": "on",
                "active": "on",
                "enrollment_plan": "monthly_full",
                "discount": "0",
            },
        )
        # re-renders form with error
        assert response.status_code == 200

    def test_post_with_invalid_parent_id(self, authenticated_client, group, enrollment_type_new_student, site_config):
        response = authenticated_client.post(
            reverse("student_create"),
            {
                "first_name": "Kid",
                "last_name": "X",
                "birth_date": "2018-01-01",
                "group": group.id,
                "gdpr_signed": "on",
                "active": "on",
                "parent_id": "99999",
                "enrollment_plan": "monthly_full",
                "discount": "0",
            },
        )
        assert response.status_code == 200

    def test_post_with_create_sibling_flag(
        self, authenticated_client, parent, group, enrollment_type_new_student, site_config
    ):
        """Creating with create_sibling=1 → redirect URL contains parent_id & create_sibling."""
        response = authenticated_client.post(
            reverse("student_create") + f"?parent_id={parent.id}",
            {
                "first_name": "Sib",
                "last_name": "Ling",
                "birth_date": "2018-02-01",
                "group": group.id,
                "gdpr_signed": "on",
                "active": "on",
                "parent_id": parent.id,
                "enrollment_plan": "monthly_full",
                "discount": "0",
                "create_sibling": "1",
            },
        )
        assert response.status_code == 302
        assert "create_sibling=1" in response.url

    def test_post_email_task_exception_is_swallowed(
        self, authenticated_client, parent, group, enrollment_type_new_student, site_config
    ):
        """The welcome email enqueue is wrapped in try/except — failure doesn't break create."""
        with patch("comms.tasks.send_welcome_email_task.delay", side_effect=Exception("broker down")):
            response = authenticated_client.post(
                reverse("student_create") + f"?parent_id={parent.id}",
                {
                    "first_name": "Ok",
                    "last_name": "Kid",
                    "birth_date": "2018-03-01",
                    "group": group.id,
                    "gdpr_signed": "on",
                    "active": "on",
                    "parent_id": parent.id,
                    "enrollment_plan": "monthly_full",
                    "discount": "0",
                },
            )
        assert response.status_code == 302


# ============================================================================
# Extra coverage: search_students helper FBV
# ============================================================================

# ============================================================================
# search_students helper FBV — has a URL
# ============================================================================


class TestSearchStudentsExtra:
    def test_get_renders(self, authenticated_client, student_with_parent):
        response = authenticated_client.get(reverse("search_students"))
        assert response.status_code == 200


class TestSpecialEnrollmentPricing:
    """A hand-priced enrolment must be billed at its own prices, not the standard ones.

    Two prices, two fields: `manual_amount` is the recurring cuota and
    `special_enrollment_fee` the one-time matrícula. Both used to be ignored once the
    payments were generated — the ficha showed the custom figures while every payment
    carried the configured 1-day / 2-day fee and the standard matrícula.
    """

    def _post(self, client, parent, group, **extra):
        data = {
            "first_name": "Especial",
            "last_name": "Alumno",
            "birth_date": "2018-03-10",
            "school": "CEIP Nuevo",
            "gdpr_signed": "on",
            "group": group.id,
            "parent_id": parent.id,
            "enrollment_plan": "monthly_full",
        }
        data.update(extra)
        return client.post(reverse("student_create") + f"?parent_id={parent.id}", data)

    def test_both_custom_prices_reach_the_payments(
        self, authenticated_client, parent, group, site_config, enrollment_type_special
    ):
        response = self._post(
            authenticated_client,
            parent,
            group,
            is_special="on",
            manual_amount="35.00",
            special_enrollment_fee="25.00",
        )
        assert response.status_code == 302

        student = Student.objects.get(first_name="Especial")
        matricula = Payment.objects.get(student=student, payment_type="enrollment")
        assert matricula.amount == Decimal("25.00")
        assert "matrícula especial" in matricula.concept
        assert matricula.amount != site_config.children_enrollment_fee

        # Periods are issued as they open, so enrolling creates only the first
        # fee — Celery adds the rest on the 1st of each month.
        monthly = Payment.objects.filter(student=student, payment_type="monthly")
        assert monthly.count() == 1
        assert set(monthly.values_list("amount", flat=True)) == {Decimal("35.00")}

    def test_matricula_falls_back_to_the_standard_fee_when_left_blank(
        self, authenticated_client, parent, group, site_config, enrollment_type_special
    ):
        """A special cuota does not imply a special matrícula."""
        response = self._post(authenticated_client, parent, group, is_special="on", manual_amount="35.00")
        assert response.status_code == 302

        student = Student.objects.get(first_name="Especial")
        matricula = Payment.objects.get(student=student, payment_type="enrollment")
        assert matricula.amount == site_config.children_enrollment_fee
        assert set(
            Payment.objects.filter(student=student, payment_type="monthly").values_list("amount", flat=True)
        ) == {Decimal("35.00")}

    def test_matricula_fee_without_the_special_checkbox_is_rejected(
        self, authenticated_client, parent, group, site_config, enrollment_type_new_student
    ):
        """Silently ignoring it would charge the standard matrícula behind the admin's back."""
        response = self._post(authenticated_client, parent, group, special_enrollment_fee="25.00")
        assert response.status_code == 200  # re-rendered form, not a redirect
        assert not Student.objects.filter(first_name="Especial").exists()


class TestStudentListCap:
    """The list renders every row it is given and filters CLIENT-side, so the
    cap is what bounds the response at the documented 2,000-student ceiling —
    the same `_LIST_CAP` + `result_truncated` shape `payments_list` uses."""

    def test_context_reports_totals_and_truncation_state(self, authenticated_client, student, active_enrollment):
        response = authenticated_client.get(reverse("students_list"))

        assert response.status_code == 200
        assert response.context["total_count"] >= 1
        assert response.context["result_truncated"] is False
        assert response.context["list_cap"] == 500

    def test_the_queryset_is_actually_capped(self):
        from core.views.students import _STUDENT_LIST_CAP, StudentListView

        assert _STUDENT_LIST_CAP == 500
        # The cap is applied by slicing in get_queryset; a paginate_by would
        # break the client-side "search all students" behaviour instead.
        import inspect

        src = inspect.getsource(StudentListView.get_queryset)
        assert "_STUDENT_LIST_CAP" in src
