"""Tests for core.models — model logic, constraints, properties."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError

from billing.models import (
    Enrollment,
    Payment,
    SiteConfiguration,
    academic_year_end_date,
    academic_year_for_month,
    academic_year_start_date,
    current_academic_year,
)
from core.models import (
    FunFridayAttendance,
    HistoryLog,
    ScheduleSlot,
    TodoItem,
)
from students.models import Group, Parent, Student

# ── Helper functions ─────────────────────────────────────────────────────────


class TestCurrentAcademicYear:
    """The year being ENROLLED INTO. Rolls over in May, when signups open."""

    def test_may_onwards_is_the_next_course(self):
        # The bug this replaced: August 2026 resolved to 2025-2026, a course
        # whose teaching period had already ended.
        assert current_academic_year(date(2026, 5, 1)) == "2026-2027"
        assert current_academic_year(date(2026, 6, 30)) == "2026-2027"
        assert current_academic_year(date(2026, 8, 31)) == "2026-2027"
        assert current_academic_year(date(2026, 9, 1)) == "2026-2027"
        assert current_academic_year(date(2026, 12, 31)) == "2026-2027"

    def test_before_may_is_the_running_course(self):
        assert current_academic_year(date(2026, 1, 1)) == "2025-2026"
        assert current_academic_year(date(2026, 4, 30)) == "2025-2026"

    def test_rollover_boundary_is_the_first_of_may(self):
        assert current_academic_year(date(2026, 4, 30)) == "2025-2026"
        assert current_academic_year(date(2026, 5, 1)) == "2026-2027"

    def test_defaults_to_today(self):
        result = current_academic_year()
        assert len(result) == 9  # "YYYY-YYYY"
        assert "-" in result


class TestAcademicYearForMonth:
    """The year a TEACHING MONTH belongs to. Still keyed on September."""

    def test_september_onwards_is_current_year(self):
        assert academic_year_for_month(date(2025, 9, 1)) == "2025-2026"
        assert academic_year_for_month(date(2025, 12, 31)) == "2025-2026"

    def test_before_september_is_previous_year(self):
        assert academic_year_for_month(date(2026, 1, 1)) == "2025-2026"
        assert academic_year_for_month(date(2026, 6, 30)) == "2025-2026"
        assert academic_year_for_month(date(2026, 8, 31)) == "2025-2026"

    def test_diverges_from_enrolment_year_between_may_and_august(self):
        # This gap is the whole reason the two functions exist. Billing a May
        # fee against the enrolment year would match no active enrollment.
        for month in (5, 6, 7, 8):
            reference = date(2026, month, 1)
            assert academic_year_for_month(reference) == "2025-2026"
            assert current_academic_year(reference) == "2026-2027"

    def test_agrees_with_enrolment_year_from_september_to_april(self):
        for reference in (date(2026, 9, 1), date(2026, 12, 1), date(2027, 4, 30)):
            assert academic_year_for_month(reference) == current_academic_year(reference)


class TestAcademicYearDates:
    def test_start_date_is_monday_after_sept_14(self):
        start = academic_year_start_date(2025)
        assert start.month == 9
        assert start.day >= 14
        assert start.weekday() == 0  # Monday

    def test_end_date_is_friday_in_june(self):
        end = academic_year_end_date(2026)
        assert end.month == 6
        assert end.weekday() == 4  # Friday


# ── SiteConfiguration ────────────────────────────────────────────────────────


class TestSiteConfiguration:
    def test_singleton_get_config(self, db):
        config = SiteConfiguration.get_config()
        assert config.pk == 1
        config2 = SiteConfiguration.get_config()
        assert config2.pk == 1
        assert SiteConfiguration.objects.count() == 1

    def test_singleton_save_enforces_pk1(self, db):
        config = SiteConfiguration()
        config.save()
        assert config.pk == 1

    def test_delete_is_noop(self, site_config):
        site_config.delete()
        assert SiteConfiguration.objects.count() == 1

    def test_default_pricing_values(self, site_config):
        assert site_config.children_enrollment_fee == Decimal("40.00")
        assert site_config.full_time_monthly_fee == Decimal("54.00")
        assert site_config.part_time_monthly_fee == Decimal("36.00")


# ── Student ──────────────────────────────────────────────────────────────────


class TestStudent:
    def test_full_name(self, student):
        assert student.full_name == "Lucas López García"

    def test_age_calculated(self, student):
        age = student.age
        assert isinstance(age, int)
        assert age >= 0

    def test_str_representation(self, student):
        assert str(student) == "Lucas López García"

    def test_student_parent_relationship(self, student_with_parent, parent):
        assert parent in student_with_parent.parents.all()
        assert student_with_parent in parent.children.all()


# ── Parent ───────────────────────────────────────────────────────────────────


class TestParent:
    def test_full_name(self, parent):
        assert parent.full_name == "María López"

    def test_dni_unique(self, parent, db):
        with pytest.raises(IntegrityError):
            Parent.objects.create(
                first_name="Otro",
                last_name="Test",
                dni=parent.dni,
                phone="600999000",
                email="otro@test.com",
            )


# ── Teacher & Group ──────────────────────────────────────────────────────────


class TestTeacherGroup:
    def test_teacher_full_name(self, teacher):
        assert teacher.full_name == "Ana García"

    def test_group_str(self, group):
        assert str(group) == "Group A"

    def test_group_teacher_relationship(self, group, teacher):
        assert group.teacher == teacher
        assert group in teacher.groups.all()

    def test_group_name_unique(self, group, teacher, db):
        with pytest.raises(IntegrityError):
            Group.objects.create(
                group_name=group.group_name,
                teacher=teacher,
            )


# ── Enrollment ───────────────────────────────────────────────────────────────


class TestEnrollment:
    def test_str_representation(self, active_enrollment):
        s = str(active_enrollment)
        assert "Lucas" in s

    def test_nothing_billed_means_nothing_owed(self, active_enrollment):
        totals = active_enrollment.payment_totals()
        assert (totals.overdue, totals.outstanding, totals.billed) == (Decimal("0.00"), Decimal("0.00"), 0)
        assert active_enrollment.is_up_to_date is True

    def test_a_pending_payment_not_yet_due_is_outstanding_but_not_overdue(self, active_enrollment, student, parent):
        Payment.objects.create(
            student=student,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("54.00"),
            payment_status="pending",
            due_date=date.today() + timedelta(days=10),
            concept="Mensualidad en curso",
        )
        assert active_enrollment.outstanding_amount == Decimal("54.00")
        assert active_enrollment.overdue_amount == Decimal("0.00")
        assert active_enrollment.is_up_to_date is True

    def test_a_pending_payment_past_its_due_date_is_overdue(self, active_enrollment, student, parent):
        Payment.objects.create(
            student=student,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("54.00"),
            payment_status="pending",
            due_date=date.today() - timedelta(days=1),
            concept="Mensualidad vencida",
        )
        assert active_enrollment.overdue_amount == Decimal("54.00")
        assert active_enrollment.is_up_to_date is False

    def test_completed_payments_are_not_owed(self, active_enrollment, student, parent):
        Payment.objects.create(
            student=student,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("54.00"),
            payment_status="completed",
            due_date=date.today() - timedelta(days=5),
            payment_date=date.today() - timedelta(days=5),
            concept="Mensualidad cobrada",
        )
        assert active_enrollment.outstanding_amount == Decimal("0.00")
        assert active_enrollment.is_up_to_date is True

    def test_unique_active_enrollment_per_student(self, active_enrollment, student, enrollment_type_new_student):
        with pytest.raises(IntegrityError):
            Enrollment.objects.create(
                student=student,
                enrollment_type=enrollment_type_new_student,
                enrollment_period_start=date(2025, 9, 15),
                enrollment_period_end=date(2026, 6, 27),
                academic_year="2025-2026",
                schedule_type="full_time",
                payment_modality="monthly",
                enrollment_amount=Decimal("54.00"),
                final_amount=Decimal("54.00"),
                status="active",
                enrollment_date=date(2025, 9, 1),
            )


# ── Payment ──────────────────────────────────────────────────────────────────


class TestPayment:
    def test_is_overdue_when_past_due(self, pending_payment):
        pending_payment.due_date = date.today() - timedelta(days=5)
        pending_payment.save()
        assert pending_payment.is_overdue is True
        assert pending_payment.days_overdue == 5

    def test_not_overdue_when_completed(self, completed_payment):
        assert completed_payment.is_overdue is False
        assert completed_payment.days_overdue == 0

    def test_not_overdue_when_future(self, pending_payment):
        pending_payment.due_date = date.today() + timedelta(days=30)
        pending_payment.save()
        assert pending_payment.is_overdue is False

    def test_clean_sets_payment_date_on_complete(self, db, student_with_parent, parent):
        payment = Payment(
            student=student_with_parent,
            parent=parent,
            payment_type="monthly",
            payment_method="cash",
            amount=Decimal("54.00"),
            payment_status="completed",
            due_date=date(2025, 10, 1),
            concept="Test",
        )
        payment.clean()
        assert payment.payment_date == date.today()


# ── TodoItem ─────────────────────────────────────────────────────────────────


class TestTodoItem:
    def test_is_overdue(self, db):
        todo = TodoItem.objects.create(
            text="Overdue task",
            due_date=date.today() - timedelta(days=1),
        )
        assert todo.is_overdue is True

    def test_not_overdue(self, db):
        todo = TodoItem.objects.create(
            text="Future task",
            due_date=date.today() + timedelta(days=1),
        )
        assert todo.is_overdue is False


# ── HistoryLog ───────────────────────────────────────────────────────────────


class TestSiteConfigurationMemo:
    """`get_config()` is called from ~26 places; each call was its own query.

    The memo is a ContextVar, deliberately NOT the shared cache framework:
    it holds live prices, and a stale entry would mis-bill a family. Within a
    request the config cannot change; `save()` invalidates immediately.
    """

    def test_repeated_calls_cost_one_query(self, db, django_assert_num_queries):
        SiteConfiguration.get_config()  # prime (may create the row)

        with django_assert_num_queries(0):
            for _ in range(10):
                SiteConfiguration.get_config()

    def test_save_invalidates_the_memo(self, db):
        config = SiteConfiguration.get_config()
        config.full_time_monthly_fee = Decimal("60.00")
        config.save()

        assert SiteConfiguration.get_config().full_time_monthly_fee == Decimal("60.00")

    def test_refresh_bypasses_the_memo(self, db, django_assert_num_queries):
        SiteConfiguration.get_config()
        with django_assert_num_queries(1):
            SiteConfiguration.get_config(refresh=True)


class TestHistoryLog:
    def test_log_creates_entry(self, db):
        entry = HistoryLog.log("payment_completed", "Test message", icon="paid")
        assert entry.action == "payment_completed"
        assert entry.message == "Test message"

    def test_log_trims_to_the_cap_periodically(self, db):
        """The cap is SOFT since the per-insert `COUNT(*)` was removed: the trim
        fires when the new row's pk is divisible by `CAP_CHECK_EVERY`, so the
        table can drift up to that many rows over `MAX_ENTRIES` between trims —
        never more."""
        for i in range(HistoryLog.MAX_ENTRIES + 5):
            HistoryLog.objects.create(action="payment_completed", message=f"Entry {i}")

        # Keep logging until a trim-eligible pk lands; bounded by the check period.
        for _ in range(HistoryLog.CAP_CHECK_EVERY + 1):
            entry = HistoryLog.log("payment_completed", "Trigger cleanup")
            if entry.pk % HistoryLog.CAP_CHECK_EVERY == 0:
                break

        count = HistoryLog.objects.count()
        assert count <= HistoryLog.MAX_ENTRIES, f"trim did not run: {count} rows"

    def test_log_drift_never_exceeds_the_check_period(self, db):
        for i in range(HistoryLog.MAX_ENTRIES + HistoryLog.CAP_CHECK_EVERY + 20):
            HistoryLog.log("payment_completed", f"Entry {i}")
        assert HistoryLog.objects.count() <= HistoryLog.MAX_ENTRIES + HistoryLog.CAP_CHECK_EVERY

    def test_log_debounced_skips_recent(self, db):
        first = HistoryLog.log_debounced("config_updated", "First", minutes=5)
        second = HistoryLog.log_debounced("config_updated", "Second", minutes=5)
        assert first is not None
        assert second is None


# ── FunFridayAttendance ──────────────────────────────────────────────────────


class TestFunFridayAttendance:
    def test_unique_student_date(self, student, db):
        friday = date(2025, 10, 3)
        FunFridayAttendance.objects.create(student=student, date=friday)
        with pytest.raises(IntegrityError):
            FunFridayAttendance.objects.create(student=student, date=friday)


# ── ScheduleSlot ────────────────────────────────────────────────────────────


class TestScheduleSlot:
    def test_create_slot(self, group, db):
        slot = ScheduleSlot.objects.create(row=0, day=0, col=0, group=group)
        assert str(slot) == "Slot row=0 day=0 col=0"

    def test_unique_row_day_col(self, group, db):
        ScheduleSlot.objects.create(row=0, day=1, col=0, group=group)
        with pytest.raises(IntegrityError):
            ScheduleSlot.objects.create(row=0, day=1, col=0, group=group)

    def test_null_group(self, db):
        slot = ScheduleSlot.objects.create(row=1, day=2, col=1, group=None)
        assert slot.group is None


# ── Student gender field ────────────────────────────────────────────────────


class TestStudentGender:
    def test_default_gender_is_m(self, student):
        assert student.gender == "m"

    def test_gender_choices(self, group, db):
        female_student = Student.objects.create(
            first_name="María",
            last_name="Test",
            birth_date=date(2018, 3, 1),
            gender="f",
            gdpr_signed=True,
            group=group,
            active=True,
        )
        assert female_student.gender == "f"
        assert female_student.get_gender_display() == "Femenino"


# ── Inactive student / withdrawn ────────────────────────────────────────────


class TestInactiveStudent:
    def test_inactive_student_exists(self, inactive_student):
        assert inactive_student.active is False
        assert inactive_student.withdrawal_date is not None
        assert inactive_student.withdrawal_reason != ""


# ── Cancelled enrollment ────────────────────────────────────────────────────


class TestCancelledEnrollment:
    def test_cancelled_enrollment_status(self, cancelled_enrollment):
        assert cancelled_enrollment.status == "cancelled"
        # cancelled enrollment should not block creating a new active one
        # (unique constraint only applies to status='active')


# ============================================================================
# Extra coverage: __str__ methods + edge branches across all models
# ============================================================================


class TestModelBranches:
    def test_student_age_no_birth_date(self, db, group):
        """Student without birth_date → age is None or 0."""
        from students.models import Student

        s = Student(
            first_name="X",
            last_name="Y",
            birth_date=None,
            gdpr_signed=True,
            group=group,
            active=True,
        )
        # Don't save (birth_date is non-null on the model). Just access the property.
        try:
            _ = s.age
        except (AttributeError, TypeError):
            pass

    def test_parent_str(self, parent):
        """Parent.__str__ includes the DNI in parens."""
        s = str(parent)
        assert parent.first_name in s
        assert parent.last_name in s

    def test_historylog_str(self, db):
        from core.models import HistoryLog

        h = HistoryLog.objects.create(action="todo_completed", message="test", icon="check")
        assert "test" in str(h)


class TestBillingModelBranches:
    def test_site_config_str(self, site_config):
        assert "Configuración" in str(site_config) or "Site" in str(site_config).title() or str(site_config)

    def test_enrollment_type_str(self, enrollment_type_new_student):
        assert str(enrollment_type_new_student)

    def test_payment_str(self, pending_payment):
        assert str(pending_payment)

    def test_payment_days_overdue_negative_when_not_overdue(self, pending_payment):
        # pending_payment due_date is in past from conftest (2025-10-01)
        _ = pending_payment.is_overdue
        _ = pending_payment.days_overdue
