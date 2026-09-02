"""Query-cost regressions and the DB-level idempotency guarantees.

Two kinds of test, both pinning things nothing else in the suite can see.

**Query cost.** A correct page and an N+1 page are indistinguishable from their
output, so every fix in this area is one refactor away from silently coming back.
Each test here seeds enough rows that a per-row query would blow the budget, and
asserts a ceiling that does NOT scale with the row count. The budgets are
deliberately loose (they include the session, the auth user, the context
processor and the page's own legitimate queries) — the point is the *shape*, not
the exact number.

The recurring cause is always one of two things, and both are invisible on a
small dev database:

* a `.filter()` / `.first()` / `.values_list()` called on a related manager that
  was already prefetched, which builds a NEW queryset and discards the cache;
* a model `@property` that runs `.count()`, read once per row in a template.

**Idempotency.** `PaymentService.pending_periods` and
`expense_service._create_if_absent` both decide whether to write by reading
first. That is a race, and Cloud Run Jobs retry on failure, so the partial
unique indexes added in billing/0010 are what actually prevent a family being
double-billed. These tests check the constraint bites on the duplicate AND that
it still permits the legitimate shapes the academy really has.
"""

import os
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.models import Prefetch
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from billing.models import Enrollment, Expense, Payment
from core.models import BacklogTask, Feature, FunFridayAttendance
from students.models import Group, Parent, Student, StudentParent

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_groups(teacher, n, capped=True):
    return [
        Group.objects.create(group_name=f"QC{i}", teacher=teacher, max_students=8 if capped else 0, active=True)
        for i in range(n)
    ]


def _make_waiting_students(groups, per_group=2):
    made = []
    for gi, g in enumerate(groups):
        for k in range(per_group):
            made.append(
                Student.objects.create(
                    first_name=f"W{gi}_{k}",
                    last_name="Espera",
                    group=g,
                    is_waiting=True,
                    waiting_since=date.today() - timedelta(days=gi),
                )
            )
    return made


# ---------------------------------------------------------------------------
# Query cost — pages
# ---------------------------------------------------------------------------


class TestWaitingListPageDoesNotScaleWithGroups:
    """`waiting_list_view` looped over Groups reading four `.count()` properties.

    `enrolled_count`, `waiting_count`, `available_spots` and `is_full` are all
    uncached, and the last two each recompute `enrolled_count` — four queries per
    group. The template then read `student.group.is_full` and
    `.available_spots` per waiting student, for four more each. Measured at 87
    queries for 13 groups and 24 waiting students.

    `group_capacity_summary()` had existed since v1.1 to avoid exactly this, with
    a docstring saying so, and this view simply never called it.
    """

    def test_page_is_flat_in_the_number_of_groups(self, authenticated_client, teacher, django_assert_max_num_queries):
        groups = _make_groups(teacher, 12)
        _make_waiting_students(groups, per_group=2)

        with django_assert_max_num_queries(15):
            assert authenticated_client.get(reverse("waiting_list")).status_code == 200

    def test_capacity_is_still_rendered(self, authenticated_client, teacher):
        group = _make_groups(teacher, 1)[0]
        # Fill it so the "Completo" branch is the one exercised.
        for i in range(group.max_students):
            Student.objects.create(first_name=f"Full{i}", last_name="Kid", group=group, active=True)
        _make_waiting_students([group], per_group=1)

        html = authenticated_client.get(reverse("waiting_list")).content.decode()
        assert "Completo" in html
        assert group.group_name in html

    def test_free_spots_are_still_rendered(self, authenticated_client, teacher):
        group = _make_groups(teacher, 1)[0]
        Student.objects.create(first_name="One", last_name="Kid", group=group, active=True)
        _make_waiting_students([group], per_group=1)

        html = authenticated_client.get(reverse("waiting_list")).content.decode()
        # 8 places, 1 taken (the waiting entry does not occupy one).
        assert "7 libres" in html


class TestManagementPageDoesNotScaleWithGroups:
    """`management.html` read `group.enrolled_count` twice plus `group.is_full`
    per row — three `.count()` queries each, 37 for 13 groups."""

    def test_page_is_flat_in_the_number_of_groups(
        self, authenticated_client, teacher, site_config, django_assert_max_num_queries
    ):
        _make_groups(teacher, 12)

        with django_assert_max_num_queries(15):
            assert authenticated_client.get(reverse("management")).status_code == 200

    def test_group_occupancy_is_still_rendered(self, authenticated_client, teacher, site_config):
        group = _make_groups(teacher, 1)[0]
        Student.objects.create(first_name="Solo", last_name="Kid", group=group, active=True)

        html = authenticated_client.get(reverse("management")).content.decode()
        assert group.group_name in html
        assert "1/8 estudiantes" in html
        assert teacher.full_name in html

    def test_uncapped_group_reports_no_cap(self, authenticated_client, teacher, site_config):
        group = _make_groups(teacher, 1, capped=False)[0]

        html = authenticated_client.get(reverse("management")).content.decode()
        assert group.group_name in html
        assert "(sin cupo)" in html


class TestExpensesPageDoesNotScaleWithRows:
    """`{% if e.generated_from %}` touched the FK descriptor once per row.

    Invisible on a database where nothing has been materialised yet: a NULL FK
    short-circuits, so the N+1 only appears once recurring expenses exist —
    i.e. in production, not in dev.
    """

    def test_page_is_flat_in_the_number_of_generated_expenses(
        self, authenticated_client, django_assert_max_num_queries
    ):
        today = date.today()
        template = Expense.objects.create(
            description="Alquiler",
            category="rent",
            amount=Decimal("500.00"),
            expense_date=today,
            is_recurring=True,
            recurring_frequency="monthly",
            recurring_day=1,
        )
        # One template can only materialise once per date, so vary the day.
        for i in range(20):
            Expense.objects.create(
                description="Alquiler",
                category="rent",
                amount=Decimal("500.00"),
                expense_date=today.replace(day=1) + timedelta(days=i),
                is_recurring=False,
                generated_from=template,
            )

        with django_assert_max_num_queries(15):
            response = authenticated_client.get(reverse("expenses_list"), {"month": today.month, "year": today.year})
        assert response.status_code == 200
        assert "(recurrente)" in response.content.decode()


class TestBacklogDashboardDoesNotScaleWithTasks:
    """`testing_tools.html` renders `task.feature.title` for any task broken out
    of a development, and `_backlog_tasks_qs()` did not join `feature`."""

    def test_feature_titles_come_from_one_query(self, django_assert_max_num_queries):
        from core.views.testing_tools import _backlog_tasks_qs

        feature = Feature.objects.create(title="Epic", description="d", status="open")
        for i in range(30):
            BacklogTask.objects.create(title=f"T{i}", description="d", priority="low", status="open", feature=feature)

        with django_assert_max_num_queries(1):
            titles = [t.feature.title for t in _backlog_tasks_qs()[:30]]
        assert titles == ["Epic"] * 30


class TestEnrollmentChangelistDoesNotScaleWithRows:
    """`payment_status_display` called `Enrollment.payment_totals()` per row.

    `prefetch_related` cannot fix it: `payment_totals` reads
    `self.payments.values_list(...)`, and `values_list()` on a related manager
    builds a fresh queryset that ignores the prefetch cache. The figures are now
    annotated onto the changelist query. Measured at 113 queries before.
    """

    @pytest.fixture
    def admin_client_(self, client):
        user = User.objects.create_superuser("qcadmin", "qc@example.com", "pw")
        client.force_login(user)
        session = client.session
        session["is_authenticated"] = True
        session.save()
        return client

    def test_changelist_is_flat_in_the_number_of_enrollments(
        self,
        admin_client_,
        teacher,
        enrollment_type_new_student,
        site_config,
        django_assert_max_num_queries,
    ):
        group = _make_groups(teacher, 1)[0]
        today = date.today()
        for i in range(20):
            kid = Student.objects.create(first_name=f"E{i}", last_name="Kid", group=group, active=True)
            enrollment = Enrollment.objects.create(
                student=kid,
                enrollment_type=enrollment_type_new_student,
                enrollment_period_start=date(today.year, 9, 1),
                enrollment_period_end=date(today.year + 1, 6, 30),
                academic_year=f"{today.year}-{today.year + 1}",
                schedule_type="full_time",
                payment_modality="monthly",
                enrollment_amount=Decimal("54.00"),
                discount_percentage=Decimal("0.00"),
                final_amount=Decimal("54.00"),
                status="active",
                enrollment_date=date(today.year, 9, 1),
            )
            Payment.objects.create(
                student=kid,
                enrollment=enrollment,
                payment_type="monthly",
                payment_method="transfer",
                amount=Decimal("54.00"),
                payment_status="pending",
                due_date=today - timedelta(days=40),
                concept="Overdue",
            )

        with django_assert_max_num_queries(20):
            response = admin_client_.get(reverse("admin:billing_enrollment_changelist"))
        assert response.status_code == 200
        # The overdue branch must still be the one rendered.
        assert "Vencido" in response.content.decode()

    def test_annotated_and_unannotated_totals_agree(
        self, admin_client_, active_enrollment, pending_payment, completed_payment
    ):
        """The annotation is a second implementation of `payment_totals()`.

        If the two ever disagree the admin column silently lies, so compare them
        on the same row rather than trusting either alone.
        """
        from django.contrib.admin.sites import AdminSite

        from billing.admin import EnrollmentAdmin

        model_admin = EnrollmentAdmin(Enrollment, AdminSite())
        request = type("R", (), {"GET": {}, "user": None})()
        annotated = model_admin.get_queryset(request).get(pk=active_enrollment.pk)
        plain = active_enrollment.payment_totals()

        assert annotated._billed == plain.billed
        assert annotated._outstanding == plain.outstanding
        assert annotated._overdue == plain.overdue


class TestFinancialSummaryYearIsConstant:
    """`financial_summary_year` called `financial_summary_month` twelve times, and
    each runs four aggregates — 48 queries for the /reports/ chart regardless of
    how much data exists."""

    def test_three_queries_regardless_of_rows(self, student, parent, active_enrollment, django_assert_max_num_queries):
        from core.services.analytics_service import financial_summary_year

        year = date.today().year
        for month in range(1, 13):
            Payment.objects.create(
                student=student,
                parent=parent,
                enrollment=active_enrollment,
                payment_type="other",
                payment_method="cash",
                amount=Decimal("10.00"),
                payment_status="completed",
                due_date=date(year, month, 15),
                payment_date=date(year, month, 15),
                concept=f"m{month}",
            )
            Expense.objects.create(
                description=f"E{month}",
                category="other",
                amount=Decimal("4.00"),
                expense_date=date(year, month, 15),
                is_recurring=False,
            )

        with django_assert_max_num_queries(3):
            summary = financial_summary_year(year)

        assert summary["income"] == Decimal("120.00")
        assert summary["expenses"] == Decimal("48.00")
        assert summary["net"] == Decimal("72.00")
        assert len(summary["months"]) == 12
        assert all(row["income"] == Decimal("10.00") for row in summary["months"])
        assert all(row["by_category"] == {"other": Decimal("4.00")} for row in summary["months"])

    def test_matches_the_per_month_helper(self, student, parent, active_enrollment):
        """`_months_by_month` is a second implementation of
        `financial_summary_month`; they must not drift."""
        from core.services.analytics_service import financial_summary_month, financial_summary_year

        year = date.today().year
        Payment.objects.create(
            student=student,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="other",
            payment_method="cash",
            amount=Decimal("33.00"),
            payment_status="completed",
            due_date=date(year, 4, 10),
            payment_date=date(year, 4, 10),
            concept="April",
        )
        Payment.objects.create(
            student=student,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="other",
            payment_method="cash",
            amount=Decimal("7.00"),
            payment_status="pending",
            due_date=date(year, 4, 20),
            concept="April pending",
        )
        Expense.objects.create(
            description="Rent",
            category="rent",
            amount=Decimal("12.00"),
            expense_date=date(year, 4, 2),
            is_recurring=False,
        )

        batched = financial_summary_year(year)["months"][3]
        one_off = financial_summary_month(4, year)
        for key in ("month", "year", "income", "pending", "expenses", "net", "by_category"):
            assert batched[key] == one_off[key], key


# ---------------------------------------------------------------------------
# Query cost — the prefetch-defeating idioms
# ---------------------------------------------------------------------------


class TestPrefetchIsNotDiscarded:
    """The two idioms that silently undo a `prefetch_related`.

    Both look like ordinary code and both cost one query per row. They are
    pinned as invariants rather than through a view so the reason stays legible.
    """

    @pytest.fixture
    def family(self, teacher):
        group = _make_groups(teacher, 1)[0]
        for i in range(10):
            p = Parent.objects.create(
                first_name=f"P{i}", last_name="Q", dni=f"QC{i:07d}", phone="600", email=f"p{i}@x.test"
            )
            kid = Student.objects.create(first_name=f"K{i}", last_name="Q", group=group, active=True)
            StudentParent.objects.create(student=kid, parent=p)
        return None

    def test_filter_on_a_prefetched_manager_re_queries(self, family):
        """Documents the trap: this is what the mass-mail views used to do.

        Asserted as "cost grows with rows" rather than an exact number, so the
        test states the property instead of a snapshot.
        """
        qs = Parent.objects.filter(children__active=True).distinct().prefetch_related("children")
        with CaptureQueriesContext(connection) as captured:
            rows = [list(p.children.filter(active=True)) for p in qs]
        assert len(rows) == 10
        assert len(captured) >= 10, "expected one query per parent from the discarded prefetch"

    def test_prefetch_with_to_attr_does_not(self, family, django_assert_max_num_queries):
        from core.views.app_forms import _ACTIVE_CHILDREN_PREFETCH

        qs = Parent.objects.filter(children__active=True).distinct().prefetch_related(_ACTIVE_CHILDREN_PREFETCH)
        with django_assert_max_num_queries(2):
            names = [[s.full_name for s in p.active_children] for p in qs]
        assert len(names) == 10
        assert all(len(group) == 1 for group in names)

    def test_first_on_a_prefetched_manager_re_queries(self, family):
        """`QuerySet.first()` on an UNORDERED queryset adds `order_by("pk")`,
        which clones and drops `_result_cache`. No model here sets
        `Meta.ordering`, so a prefetched `.parents.first()` always re-queries —
        this is what made `prefetch_related("student__parents")` in
        `generate_payments` dead weight."""
        qs = Student.objects.filter(active=True).prefetch_related("parents")
        with CaptureQueriesContext(connection) as captured:
            picked = [s.parents.first() for s in qs]
        assert len(picked) == 10
        assert len(captured) >= 10, "expected one query per student from the discarded prefetch"

    def test_next_iter_uses_the_cache(self, family, django_assert_max_num_queries):
        qs = Student.objects.filter(active=True).prefetch_related("parents")
        with django_assert_max_num_queries(2):
            picked = [next(iter(s.parents.all()), None) for s in qs]
        assert len(picked) == 10
        assert all(p is not None for p in picked)

    def test_generate_payments_picks_the_lowest_pk_parent(self, student, parent, second_parent):
        """The prefetch is ordered by id on purpose: it decides which parent
        becomes the TITULAR on every generated payment, and the `.first()` it
        replaced sorted by pk."""
        StudentParent.objects.create(student=student, parent=second_parent)
        StudentParent.objects.create(student=student, parent=parent)
        lowest = min(parent.pk, second_parent.pk)

        qs = Student.objects.filter(pk=student.pk).prefetch_related(
            Prefetch("parents", queryset=Parent.objects.order_by("id"))
        )
        chosen = next(iter(next(iter(qs)).parents.all()), None)
        assert chosen.pk == lowest


class TestFunFridayDateLookupIsIndexed:
    """`get_ff_student_ids()` filters on `date` alone and runs TWICE per
    students-list and schedule page load. The `(student, date)` unique
    constraint leads on `student`, so it cannot serve that lookup — `EXPLAIN`
    showed a Seq Scan on a table that grows by one row per student per Friday.
    """

    def test_a_date_index_exists(self):
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'fun_friday_attendance' AND indexdef LIKE '%%(date)%%'"
            )
            assert cur.fetchall(), "no single-column index on fun_friday_attendance.date"

    def test_lookup_still_returns_the_right_students(self, student, group):
        from core.views.students import get_ff_student_ids

        friday = date(2026, 9, 4)
        other = Student.objects.create(first_name="Other", last_name="Kid", group=group, active=True)
        FunFridayAttendance.objects.create(student=student, date=friday)
        FunFridayAttendance.objects.create(student=other, date=friday + timedelta(days=7))

        assert get_ff_student_ids(friday) == {student.id}


# ---------------------------------------------------------------------------
# Idempotency, enforced by the database
# ---------------------------------------------------------------------------


def _monthly(student, parent, enrollment, *, due, status="pending", concept="x"):
    return Payment.objects.create(
        student=student,
        parent=parent,
        enrollment=enrollment,
        payment_type="monthly",
        payment_method="transfer",
        amount=Decimal("54.00"),
        payment_status=status,
        payment_date=due if status == "completed" else None,
        due_date=due,
        concept=concept,
    )


class TestPendingPeriodicPaymentIsUniquePerMonth:
    """`payments.unique_pending_periodic_payment_per_month`.

    `PaymentService.pending_periods` reads the already-billed due months and then
    creates what is missing. Nothing enforced that, so two overlapping
    `generate_payments` runs — and Cloud Run Jobs retry on failure — could both
    pass the check and bill a family twice.
    """

    def test_a_second_pending_month_is_rejected(self, student, parent, active_enrollment):
        first = _monthly(student, parent, active_enrollment, due=date(2026, 10, 31))
        with pytest.raises(IntegrityError), transaction.atomic():
            _monthly(student, parent, active_enrollment, due=date(2026, 10, 1), concept="dupe")
        assert Payment.objects.filter(payment_status="pending").count() == 1
        assert Payment.objects.get(payment_status="pending").pk == first.pk

    def test_full_clean_reports_it_in_spanish_without_naming_the_constraint(self, student, parent, active_enrollment):
        """`full_clean()` validates expression constraints on Django 4.1+, so this
        is the message `create_payment` actually shows an admin. Django's default
        is 'No se cumple la restricción "unique_pending_..."', which leaks the
        constraint name into the UI and is not actionable."""
        _monthly(student, parent, active_enrollment, due=date(2026, 10, 31))
        dupe = Payment(
            student=student,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("54.00"),
            payment_status="pending",
            due_date=date(2026, 10, 1),
            concept="dupe",
        )
        with pytest.raises(ValidationError) as exc:
            dupe.full_clean(exclude=["enrollment"])

        joined = " ".join(exc.value.messages)
        assert "Ya existe un pago pendiente" in joined
        assert "unique_pending" not in joined

    def test_a_completed_and_a_pending_month_coexist(self, student, parent, active_enrollment):
        """Deliberately allowed. A family paying one month part in cash and part
        by transfer, or a correction billed after a partial collection, both leave
        a completed and a pending row in the same month. Including `completed` in
        the constraint would forbid states the academy really has."""
        _monthly(student, parent, active_enrollment, due=date(2026, 10, 31), status="completed")
        _monthly(student, parent, active_enrollment, due=date(2026, 10, 1), concept="rest")
        assert Payment.objects.count() == 2

    def test_cancelling_frees_the_month_again(self, student, parent, active_enrollment):
        """Cancelling a duplicate and re-issuing it must keep working — and
        `reconcile_payment_schedule` must be able to supersede a stale row with
        one due in the same month, which is why it cancels before it creates."""
        stale = _monthly(student, parent, active_enrollment, due=date(2026, 10, 15))
        stale.payment_status = "cancelled"
        stale.save(update_fields=["payment_status", "updated_at"])

        reissued = _monthly(student, parent, active_enrollment, due=date(2026, 10, 31), concept="reissued")
        assert reissued.pk != stale.pk

    def test_non_periodic_payments_may_repeat_within_a_month(self, student, parent, active_enrollment):
        """Only `monthly` / `quarterly` are covered: a student can legitimately
        have several `enrollment` / `other` payments due in one month."""
        for n in range(3):
            Payment.objects.create(
                student=student,
                parent=parent,
                payment_type="other",
                payment_method="cash",
                amount=Decimal("5.00"),
                payment_status="pending",
                due_date=date(2026, 10, 10),
                concept=f"extra {n}",
            )
        assert Payment.objects.filter(payment_type="other").count() == 3

    def test_two_students_may_share_a_due_month(self, student, parent, active_enrollment, group):
        """The constraint is per student, not global."""
        other = Student.objects.create(first_name="Other", last_name="Kid", group=group, active=True)
        StudentParent.objects.create(student=other, parent=parent)
        _monthly(student, parent, active_enrollment, due=date(2026, 10, 31))
        Payment.objects.create(
            student=other,
            parent=parent,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("54.00"),
            payment_status="pending",
            due_date=date(2026, 10, 31),
            concept="other kid",
        )
        assert Payment.objects.filter(payment_type="monthly").count() == 2

    def test_the_scheduler_survives_losing_the_race(self, student, parent, active_enrollment, site_config):
        """`schedule_academic_year_payments` swallows the IntegrityError and keeps
        going, so one lost race does not abort the remaining periods (or, in the
        cron, the remaining students)."""
        from billing.services.payment_service import PaymentService

        first_pass = PaymentService.schedule_academic_year_payments(active_enrollment, parent, as_of=date(2026, 1, 31))
        assert first_pass > 0
        before = Payment.objects.count()

        # A re-run must be a no-op, and must not raise even though the rows exist.
        assert PaymentService.schedule_academic_year_payments(active_enrollment, parent, as_of=date(2026, 1, 31)) == 0
        assert Payment.objects.count() == before


class TestMaterialisedExpenseIsUniquePerDate:
    """`expenses.unique_materialized_expense_per_date`.

    `_create_if_absent` checks `.exists()` then creates, and the monthly and
    daily materialisers run on different cadences over overlapping templates, so
    both could pass the check and each produce a row.
    """

    @pytest.fixture
    def template(self):
        return Expense.objects.create(
            description="Alquiler",
            category="rent",
            amount=Decimal("500.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_frequency="monthly",
            recurring_day=1,
        )

    def test_a_duplicate_is_rejected(self, template):
        Expense.objects.create(
            description="Alquiler",
            category="rent",
            amount=Decimal("500.00"),
            expense_date=date(2026, 2, 1),
            is_recurring=False,
            generated_from=template,
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            Expense.objects.create(
                description="Alquiler",
                category="rent",
                amount=Decimal("500.00"),
                expense_date=date(2026, 2, 1),
                is_recurring=False,
                generated_from=template,
            )

    def test_hand_entered_expenses_are_unaffected(self):
        """`generated_from` is NULL for those, and NULLs never collide in
        Postgres — an academy can pay two separate bills on one day."""
        for n in range(3):
            Expense.objects.create(
                description=f"Compra {n}",
                category="other",
                amount=Decimal("9.00"),
                expense_date=date(2026, 2, 1),
                is_recurring=False,
            )
        assert Expense.objects.filter(generated_from__isnull=True, is_recurring=False).count() == 3

    def test_materialising_twice_creates_one_row(self, template):
        from billing.services.expense_service import materialize_recurring

        assert materialize_recurring(3, 2026) == 1
        assert materialize_recurring(3, 2026) == 0
        assert Expense.objects.filter(generated_from=template, expense_date=date(2026, 3, 1)).count() == 1


# ---------------------------------------------------------------------------
# The crons: constant cost, not one query per enrollment
# ---------------------------------------------------------------------------


def _roster(teacher, enrollment_type, n, *, birthdays_today=False):
    """`n` active enrollments, each with two parents that have an email."""
    group = Group.objects.create(group_name="Cron", teacher=teacher, max_students=0, active=True)
    today = date.today()
    made = []
    for i in range(n):
        first = Parent.objects.create(
            first_name=f"CP{i}", last_name="X", dni=f"C{i:07d}", phone="600", email=f"cp{i}@x.test"
        )
        second = Parent.objects.create(
            first_name=f"CQ{i}", last_name="X", dni=f"Cb{i:06d}", phone="601", email=f"cq{i}@x.test"
        )
        student = Student.objects.create(
            first_name=f"CS{i}",
            last_name="X",
            birth_date=date(2014, today.month, today.day) if birthdays_today else date(2014, 5, 5),
            group=group,
            active=True,
        )
        StudentParent.objects.create(student=student, parent=first)
        StudentParent.objects.create(student=student, parent=second)
        made.append(
            Enrollment.objects.create(
                student=student,
                enrollment_type=enrollment_type,
                academic_year=f"{today.year}-{today.year + 1}",
                status="active",
                schedule_type="full_time",
                payment_modality="monthly",
                enrollment_amount=Decimal("54.00"),
                discount_percentage=Decimal("0.00"),
                final_amount=Decimal("54.00"),
                enrollment_date=today,
                enrollment_period_start=date(today.year, 9, 1),
                enrollment_period_end=date(today.year + 1, 6, 30),
            )
        )
    return made


class TestPaymentCronsAreConstantCost:
    """`pending_periods` resolves one enrollment's billed months with its own
    query. Correct in isolation, but `generate_payments` and
    `reconcile_payment_schedule` both call it in a loop over EVERY active
    enrollment — measured at exactly one `SELECT payments` each, so ~2,000 round
    trips per monthly run at the roll this academy is sized for.

    `billed_months_map` resolves the whole roll in one query and both commands
    pass slices of it in. The budgets here are ABSOLUTE, not per row: that is the
    whole point.
    """

    def test_dry_run_does_not_scale(
        self, teacher, enrollment_type_new_student, site_config, django_assert_max_num_queries
    ):
        _roster(teacher, enrollment_type_new_student, 25)
        with django_assert_max_num_queries(8):
            call_command("generate_payments", "--dry-run", stdout=StringIO())

    def test_idempotent_re_run_does_not_scale(
        self, teacher, enrollment_type_new_student, site_config, django_assert_max_num_queries
    ):
        _roster(teacher, enrollment_type_new_student, 25)
        call_command("generate_payments", stdout=StringIO())
        # The second run has nothing to create, so only the bulk reads remain.
        with django_assert_max_num_queries(8):
            call_command("generate_payments", stdout=StringIO())

    def test_reconcile_dry_run_does_not_scale(
        self, teacher, enrollment_type_new_student, site_config, django_assert_max_num_queries
    ):
        """Dry run is this command's DEFAULT mode, and it used to open a savepoint
        per enrollment to protect writes that are all behind `if apply_changes`.
        The safety net is now one outer transaction instead of one per row."""
        _roster(teacher, enrollment_type_new_student, 25)
        with django_assert_max_num_queries(10):
            call_command("reconcile_payment_schedule", stdout=StringIO())

    def test_a_dry_run_still_writes_nothing(self, teacher, enrollment_type_new_student, site_config):
        """The outer transaction rolls back, so the guard was not lost when the
        per-enrollment savepoints went away."""
        _roster(teacher, enrollment_type_new_student, 3)
        call_command("generate_payments", stdout=StringIO())
        before = set(Payment.objects.values_list("id", flat=True))

        call_command("reconcile_payment_schedule", "--cancel-stale", stdout=StringIO())

        assert set(Payment.objects.values_list("id", flat=True)) == before
        assert not Payment.objects.filter(payment_status="cancelled").exists()

    def test_batching_does_not_change_what_gets_billed(self, teacher, enrollment_type_new_student, site_config):
        enrollments = _roster(teacher, enrollment_type_new_student, 5)
        call_command("generate_payments", stdout=StringIO())
        billed = {
            e.pk: sorted(Payment.objects.filter(enrollment=e).values_list("due_date", flat=True)) for e in enrollments
        }
        assert all(dues for dues in billed.values()), "every enrollment should have been billed"

        call_command("generate_payments", stdout=StringIO())
        assert {
            e.pk: sorted(Payment.objects.filter(enrollment=e).values_list("due_date", flat=True)) for e in enrollments
        } == billed


class TestBilledMonthsMap:
    def test_matches_the_per_enrollment_query(self, teacher, enrollment_type_new_student, site_config):
        """`billed_months_map` is a second implementation of the set
        `pending_periods` builds for itself. If the two disagree the batched cron
        bills differently from the unbatched one, which is worse than either."""
        from billing.services.payment_service import PaymentService

        enrollments = _roster(teacher, enrollment_type_new_student, 6)
        call_command("generate_payments", stdout=StringIO())

        mapped = PaymentService.billed_months_map({e.student_id for e in enrollments})
        for e in enrollments:
            expected = {
                (d.month, d.year)
                for d in Payment.objects.filter(student=e.student, payment_type="monthly").values_list(
                    "due_date", flat=True
                )
                if d is not None
            }
            assert mapped.get((e.student_id, "monthly"), set()) == expected

    def test_batched_and_unbatched_pending_periods_agree(self, teacher, enrollment_type_new_student, site_config):
        from billing.services.payment_service import PaymentService

        enrollments = _roster(teacher, enrollment_type_new_student, 6)
        call_command("generate_payments", stdout=StringIO())

        mapped = PaymentService.billed_months_map({e.student_id for e in enrollments})
        for e in Enrollment.objects.select_related("student", "enrollment_type").filter(
            pk__in=[x.pk for x in enrollments]
        ):
            batched = PaymentService.pending_periods(e, billed_months=mapped.get((e.student_id, "monthly"), set()))
            unbatched = PaymentService.pending_periods(e)
            assert [p["due"] for p in batched] == [p["due"] for p in unbatched]

    def test_an_empty_set_is_not_treated_as_absent(self, active_enrollment, site_config):
        """`None` means "resolve it yourself"; an empty set means "this student has
        nothing billed yet". Testing falsiness instead of `is None` would make the
        batched path re-query for exactly the students it had already resolved."""
        from billing.services.payment_service import PaymentService

        with CaptureQueriesContext(connection) as captured:
            PaymentService.pending_periods(active_enrollment, billed_months=set())
        assert not [q for q in captured.captured_queries if "payments" in q["sql"]]

    def test_creating_updates_the_passed_map(self, active_enrollment, parent, site_config):
        """The map is mutated as rows are created, so a second call for the same
        student in the same run sees what the first issued. Without that the
        batched path would be LESS idempotent than the unbatched one."""
        from billing.services.payment_service import PaymentService

        billed = set()
        first = PaymentService.schedule_academic_year_payments(
            active_enrollment, parent, as_of=date(2026, 1, 31), billed_months=billed
        )
        assert first > 0
        assert billed, "created periods should have been recorded in the map"

        again = PaymentService.schedule_academic_year_payments(
            active_enrollment, parent, as_of=date(2026, 1, 31), billed_months=billed
        )
        assert again == 0


class TestBirthdayFanOutDoesNotDiscardItsPrefetch:
    """Production runs `CELERY_TASK_ALWAYS_EAGER=True`, so the per-student subtask
    runs INLINE. Its `prefetch_related("parents")` was followed by
    `parents.exclude(...).values_list(...)`, which builds a new queryset and
    ignores the cache — three queries per student where two suffice. Invisible in
    development, where the subtask runs in the worker and its queries never show
    up in the parent task's count.
    """

    def test_two_queries_per_student(self, student_with_parent, django_assert_max_num_queries):
        from comms.tasks import send_birthday_email_task

        with patch("comms.services.email_service.EmailService.send_email", return_value=True):
            with django_assert_max_num_queries(2):
                send_birthday_email_task.apply(args=[student_with_parent.id]).get()

    def test_it_still_emails_every_parent_with_an_address(self, student, parent, second_parent):
        StudentParent.objects.create(student=student, parent=parent)
        StudentParent.objects.create(student=student, parent=second_parent)
        from comms.tasks import send_birthday_email_task

        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as send:
            send_birthday_email_task.apply(args=[student.id]).get()

        addressed = {call.kwargs["recipients"] for call in send.call_args_list}
        assert addressed == {parent.email, second_parent.email}

    def test_a_parent_without_an_email_is_skipped(self, student, parent):
        parent.email = ""
        parent.save(update_fields=["email"])
        StudentParent.objects.create(student=student, parent=parent)
        from comms.tasks import send_birthday_email_task

        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as send:
            result = send_birthday_email_task.apply(args=[student.id]).get()

        send.assert_not_called()
        assert result["status"] == "skipped"

    def test_an_adult_student_is_emailed_directly(self, adult_student):
        """The fallback for a student with no parent on file must survive the
        switch from `values_list` to the prefetch."""
        adult_student.email = "adult@example.com"
        adult_student.save(update_fields=["email"])
        from comms.tasks import send_birthday_email_task

        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as send:
            send_birthday_email_task.apply(args=[adult_student.id]).get()

        assert {call.kwargs["recipients"] for call in send.call_args_list} == {"adult@example.com"}


class TestDatabaseConnectionSettings:
    """The two `DATABASES` branches in settings.py drifted.

    `DATABASE_URL` (Cloud Run) passed `conn_health_checks=True`; the `POSTGRES_*`
    branch — the testing VM and every dev container — set `CONN_MAX_AGE=600` and
    never checked the connection was still alive, so a Postgres restart or a
    dropped idle socket surfaced as an OperationalError on the first query of a
    request. `settings_test.py` replaces `DATABASES` wholesale, so nothing in the
    suite exercises either branch; this loads settings.py in ISOLATION (under a
    private module name, so Django's configured settings are untouched) with and
    without `DATABASE_URL` and inspects the dict each branch actually builds.
    """

    @staticmethod
    def _load(**env):
        import importlib.util
        from pathlib import Path

        from django.conf import settings as dj_settings

        path = Path(dj_settings.BASE_DIR) / "project" / "settings.py"
        spec = importlib.util.spec_from_file_location("_probe_settings", path)
        module = importlib.util.module_from_spec(spec)
        base = {"DJANGO_DEBUG": "True", "DJANGO_SECRET_KEY": "probe-only-not-a-real-key", "ENVIRONMENT": "development"}
        with patch.dict(os.environ, {**base, **env}, clear=False):
            spec.loader.exec_module(module)
        return module.DATABASES["default"]

    @pytest.fixture
    def postgres_branch(self):
        return self._load(DATABASE_URL="")

    @pytest.fixture
    def database_url_branch(self):
        return self._load(DATABASE_URL="postgres://u:p@localhost:5432/db")

    def test_both_branches_use_persistent_connections(self, postgres_branch, database_url_branch):
        assert postgres_branch["CONN_MAX_AGE"] == 600
        assert database_url_branch["CONN_MAX_AGE"] == 600

    def test_both_branches_health_check_those_connections(self, postgres_branch, database_url_branch):
        """A persistent connection with no health check is handed to a request
        after the server has already closed it."""
        assert postgres_branch["CONN_HEALTH_CHECKS"] is True
        assert database_url_branch["CONN_HEALTH_CHECKS"] is True

    def test_both_branches_cap_statement_duration(self, postgres_branch, database_url_branch):
        for branch, name in ((postgres_branch, "POSTGRES_*"), (database_url_branch, "DATABASE_URL")):
            options = branch.get("OPTIONS") or {}
            assert "statement_timeout" in options.get("options", ""), (
                f"the {name} branch has no statement_timeout; one pathological query can hold a connection indefinitely"
            )

    def test_the_ceiling_is_overridable(self):
        """Raising it is the escape hatch for a migration that rewrites a big
        table — `AddIndex` is a single statement and would otherwise abort."""
        branch = self._load(DATABASE_URL="", DB_STATEMENT_TIMEOUT_MS="90000")
        assert "statement_timeout=90000" in branch["OPTIONS"]["options"]

    def test_the_postgres_branch_keeps_its_connect_timeout(self, postgres_branch):
        assert postgres_branch["OPTIONS"]["connect_timeout"] == 10
