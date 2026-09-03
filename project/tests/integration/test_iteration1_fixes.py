"""Regression tests for the iteration-1 full-codebase review fixes.

Each test names the fix it pins so a later refactor cannot silently undo it.
Explicit money dates use the ELAPSED 2020-2021 course (every period has started)
so amounts/counts hold on any run date — same rationale as test_payment_scheduling.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from billing.models import Enrollment, Payment, enrollment_academic_year
from billing.services.enrollment_service import EnrollmentService
from billing.services.payment_service import PaymentService

pytestmark = pytest.mark.django_db


class TestEnrollmentAcademicYear:
    """#2 — a May–August start joins the RUNNING course, not the next one."""

    def test_may_start_is_current_course(self):
        assert enrollment_academic_year(date(2026, 5, 15)) == "2025-2026"

    def test_june_start_is_current_course(self):
        assert enrollment_academic_year(date(2026, 6, 3)) == "2025-2026"

    def test_july_start_is_next_course(self):
        assert enrollment_academic_year(date(2026, 7, 10)) == "2026-2027"

    def test_august_start_is_next_course(self):
        assert enrollment_academic_year(date(2026, 8, 20)) == "2026-2027"

    def test_october_start_is_current_course(self):
        assert enrollment_academic_year(date(2025, 10, 1)) == "2025-2026"

    def test_may_starter_is_billed_for_may(self, student, enrollment_type_new_student, site_config):
        enrollment = EnrollmentService.create_enrollment(
            student, {"enrollment_plan": "monthly_full", "start_date": date(2021, 5, 10)}
        )
        assert enrollment.academic_year == "2020-2021"
        periods = PaymentService.billing_periods(enrollment)
        assert periods, "a May starter must have at least the May period"
        assert periods[0]["months"][0] == (5, 2021)


class TestZeroMatriculaSkipped:
    """#10 — a fully discounted matrícula creates no €0.00 pending payment."""

    def test_zero_fee_creates_no_payment(
        self,
        authenticated_client,
        student_with_parent,
        active_enrollment,
        enrollment_type_returning_student,
        site_config,
    ):
        # Make the returning-student discount cancel the whole matrícula.
        site_config.children_enrollment_fee = Decimal("40.00")
        site_config.returning_student_enrollment_discount = Decimal("40.00")
        site_config.save()

        from unittest import mock

        # Anchor the window so the elapsed start date passes validation.
        with mock.patch(
            "billing.models.relevant_academic_years",
            lambda reference_date=None: ["2020-2021", "2026-2027"],
        ):
            response = authenticated_client.post(
                reverse("enroll_student", args=[student_with_parent.id]),
                {"enrollment_plan": "monthly_full", "start_date": "2020-11-01", "charge_enrollment_fee": "on"},
            )
        assert response.status_code == 200
        assert not Payment.objects.filter(student=student_with_parent, payment_type="enrollment").exists(), (
            "a €0.00 matrícula must not be written"
        )


class TestModalityChangeCancelsOldSchedule:
    """#1 — switching modality cancels the superseded pending periodic rows."""

    def test_switch_cancels_other_type_pending_rows(
        self, authenticated_client, student_with_parent, active_enrollment, site_config
    ):
        # active_enrollment is monthly; give it a future pending monthly row.
        future = date.today().replace(day=28)
        if future <= date.today():
            future = future.replace(day=1)
        Payment.objects.create(
            student=student_with_parent,
            parent=student_with_parent.parents.first(),
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("54.00"),
            payment_status="pending",
            due_date=date(date.today().year + 1, 6, 30),
            concept="Mensualidad futura",
        )
        response = authenticated_client.post(
            reverse("update_enrollment_modality", args=[student_with_parent.id]),
            data='{"payment_modality": "quarterly"}',
            content_type="application/json",
        )
        assert response.status_code == 200
        active_enrollment.refresh_from_db()
        assert active_enrollment.payment_modality == "quarterly"
        # The future monthly row is cancelled so it cannot double-bill against
        # the new quarterly schedule.
        assert not Payment.objects.filter(
            student=student_with_parent, payment_type="monthly", payment_status="pending"
        ).exists()

    def test_special_enrollment_modality_change_refused(
        self, authenticated_client, student_with_parent, site_config, enrollment_type_special
    ):
        Enrollment.objects.create(
            student=student_with_parent,
            enrollment_type=enrollment_type_special,
            enrollment_period_start=date(2025, 9, 1),
            enrollment_period_end=date(2026, 6, 30),
            academic_year="2025-2026",
            schedule_type="full_time",
            payment_modality="monthly",
            enrollment_amount=Decimal("25.00"),
            final_amount=Decimal("25.00"),
            status="active",
            enrollment_date=date(2025, 9, 1),
        )
        response = authenticated_client.post(
            reverse("update_enrollment_modality", args=[student_with_parent.id]),
            data='{"payment_modality": "quarterly"}',
            content_type="application/json",
        )
        assert response.status_code == 400
        assert not response.json()["success"]


class TestParentlessStudentIsBilled:
    """#3 — a non-adult student with no parent is billed, not skipped."""

    def test_generate_payments_bills_with_null_parent(self, student, enrollment_type_new_student, site_config):
        # student has no parents linked.
        assert not student.parents.exists()
        enrollment = EnrollmentService.create_enrollment(
            student, {"enrollment_plan": "monthly_full", "start_date": date(2020, 11, 1)}
        )
        created = PaymentService.schedule_academic_year_payments(enrollment, parent=None, as_of=date(2021, 6, 30))
        assert created > 0
        monthly = Payment.objects.filter(student=student, payment_type="monthly")
        assert monthly.exists()
        assert all(p.parent_id is None for p in monthly)


class TestStripeUnpaidGuard:
    """#12 — an unpaid (SEPA) checkout.session.completed does not complete."""

    def test_unpaid_session_does_not_complete(self, pending_payment):
        pending_payment.stripe_session_id = "cs_sepa"
        pending_payment.save()
        from billing.services.stripe_service import StripeService

        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_sepa", "payment_status": "unpaid"}},
        }
        result = StripeService().apply_webhook_event(event)
        assert result["status"] == "awaiting_payment"
        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == "pending"

    def test_async_payment_succeeded_completes(self, pending_payment):
        pending_payment.stripe_session_id = "cs_sepa2"
        pending_payment.save()
        from unittest.mock import patch

        from billing.services.stripe_service import StripeService

        event = {
            "type": "checkout.session.async_payment_succeeded",
            "data": {"object": {"id": "cs_sepa2", "payment_status": "paid"}},
        }
        with patch("comms.tasks.send_payment_receipt_email_task.delay"):
            result = StripeService().apply_webhook_event(event)
        assert result["status"] == "completed"
        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == "completed"

    def test_cancelled_row_not_resurrected(self, pending_payment):
        pending_payment.stripe_session_id = "cs_dead"
        pending_payment.payment_status = "cancelled"
        pending_payment.save()
        from billing.services.stripe_service import StripeService

        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_dead", "payment_status": "paid"}},
        }
        result = StripeService().apply_webhook_event(event)
        assert result["status"] == "ignored_dead_payment"
        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == "cancelled"


class TestTeacherActiveMirrored:
    """#15 — deactivating a Teacher disables the linked auth.User login."""

    def test_deactivating_teacher_disables_user(self, db):
        from students.models import Teacher

        teacher = Teacher.objects.create(first_name="Ana", last_name="X", email="ana.mirror@x.test", active=True)
        user = teacher.ensure_user(password="whatever-123456")
        assert user.is_active is True

        teacher.active = False
        teacher.save()
        user.refresh_from_db()
        assert user.is_active is False

    def test_deleting_teacher_deactivates_orphaned_user(self, db):
        from django.contrib.auth import get_user_model

        from students.models import Teacher

        teacher = Teacher.objects.create(first_name="Bob", last_name="Y", email="bob.orphan@x.test", active=True)
        user = teacher.ensure_user(password="whatever-123456")
        uid = user.pk
        teacher.delete()
        assert get_user_model().objects.get(pk=uid).is_active is False


class TestGroupAdminCountsExcludeWaiting:
    """#50 — GroupAdmin's enrolled count ignores waiting-list students."""

    def test_waiting_students_not_counted(self, group, enrollment_type_new_student, site_config):
        from students.admin import GroupAdmin
        from students.models import Student

        Student.objects.create(first_name="Enrolled", last_name="A", group=group, active=True, is_waiting=False)
        Student.objects.create(first_name="Waiting", last_name="B", group=group, active=True, is_waiting=True)

        admin_obj = GroupAdmin(group.__class__, None)
        # Simulate the changelist queryset annotation.

        class _Req:
            GET = {}

        annotated = admin_obj.get_queryset(_Req()).get(pk=group.pk)
        assert annotated._enrolled == 1


class TestReportsSafeInt:
    """#67 — /reports/ rejects an out-of-range month like every other view."""

    def test_out_of_range_month_falls_back(self, authenticated_client):
        response = authenticated_client.get(reverse("reports_view"), {"month": 99})
        assert response.status_code == 200
        assert response.context["month"] == date.today().month


class TestGroupCapEnforcedOnWrite:
    """#76 — Group.max_students is enforced by StudentForm, not just a redirect."""

    def test_full_group_rejected_by_form(self, group, teacher):
        from students.forms import StudentForm
        from students.models import Student

        group.max_students = 1
        group.save()
        Student.objects.create(first_name="Full", last_name="A", group=group, active=True, is_waiting=False)

        form = StudentForm(
            data={
                "first_name": "Second",
                "last_name": "B",
                "birth_date": "2015-01-01",
                "gender": "m",
                "group": group.id,
            }
        )
        assert not form.is_valid()
        assert "group" in form.errors
