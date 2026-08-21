"""Integration tests for v1.1 — Waiting List & Group Capacity views."""

from datetime import date

import pytest
from django.urls import reverse

from core.models import HistoryLog
from students.models import Student

pytestmark = pytest.mark.django_db


@pytest.fixture
def waiting_student(db, group, parent):
    student = Student.objects.create(
        first_name="Waiter",
        last_name="One",
        birth_date=date(2018, 1, 1),
        gdpr_signed=True,
        group=group,
        active=True,
        is_waiting=True,
    )
    student.parents.add(parent)
    return student


class TestWaitingListView:
    def test_loads_ok(self, authenticated_client):
        response = authenticated_client.get(reverse("waiting_list"))
        assert response.status_code == 200

    def test_lists_waiting_students(self, authenticated_client, waiting_student):
        response = authenticated_client.get(reverse("waiting_list"))
        ids = {s.id for s in response.context["waiting_students"]}
        assert waiting_student.id in ids

    def test_excludes_non_waiting_students(self, authenticated_client, student):
        response = authenticated_client.get(reverse("waiting_list"))
        ids = {s.id for s in response.context["waiting_students"]}
        assert student.id not in ids

    def test_group_filter(self, authenticated_client, waiting_student, group, teacher):
        from students.models import Group

        other_group = Group.objects.create(group_name="Other Group", color="#f00", teacher=teacher, active=True)
        other_waiter = Student.objects.create(
            first_name="Waiter2",
            last_name="Two",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=other_group,
            is_waiting=True,
        )
        response = authenticated_client.get(reverse("waiting_list"), {"group": str(group.id)})
        ids = {s.id for s in response.context["waiting_students"]}
        assert waiting_student.id in ids
        assert other_waiter.id not in ids

    def test_shows_group_capacity_summary(self, authenticated_client, group):
        group.max_students = 5
        group.save()
        response = authenticated_client.get(reverse("waiting_list"))
        summary_ids = [row["group"].id for row in response.context["groups_summary"]]
        assert group.id in summary_ids


class TestAssignFromWaitingList:
    def test_requires_post(self, authenticated_client, waiting_student):
        response = authenticated_client.get(reverse("assign_from_waiting_list", args=[waiting_student.id]))
        assert response.status_code == 405

    def test_flips_is_waiting_off(self, authenticated_client, waiting_student, site_config, enrollment_type_monthly):
        response = authenticated_client.post(reverse("assign_from_waiting_list", args=[waiting_student.id]))
        assert response.status_code == 302
        waiting_student.refresh_from_db()
        assert waiting_student.is_waiting is False
        assert waiting_student.waiting_since is None

    def test_creates_default_enrollment(
        self, authenticated_client, waiting_student, site_config, enrollment_type_monthly
    ):
        authenticated_client.post(reverse("assign_from_waiting_list", args=[waiting_student.id]))
        assert waiting_student.enrollments.filter(status="active").exists()

    def test_creates_enrollment_fee_payment(
        self, authenticated_client, waiting_student, site_config, enrollment_type_monthly
    ):
        from billing.models import Payment

        before = Payment.objects.count()
        authenticated_client.post(reverse("assign_from_waiting_list", args=[waiting_student.id]))
        assert Payment.objects.filter(student=waiting_student, payment_type="enrollment").count() == 1
        assert Payment.objects.count() == before + 1

    def test_rejects_when_group_full(
        self, authenticated_client, waiting_student, group, student, site_config, enrollment_type_monthly
    ):
        group.max_students = 1  # `student` already fills it
        group.save()
        response = authenticated_client.post(reverse("assign_from_waiting_list", args=[waiting_student.id]))
        assert response.status_code == 302
        waiting_student.refresh_from_db()
        assert waiting_student.is_waiting is True  # Still waiting

    def test_logs_history(self, authenticated_client, waiting_student, site_config, enrollment_type_monthly):
        before = HistoryLog.objects.filter(action="waiting_list_assigned").count()
        authenticated_client.post(reverse("assign_from_waiting_list", args=[waiting_student.id]))
        after = HistoryLog.objects.filter(action="waiting_list_assigned").count()
        assert after == before + 1

    def test_404_when_not_waiting(self, authenticated_client, student):
        response = authenticated_client.post(reverse("assign_from_waiting_list", args=[student.id]))
        assert response.status_code == 404


class TestAddToWaitingList:
    def test_flips_is_waiting_on(self, authenticated_client, student):
        response = authenticated_client.post(reverse("add_to_waiting_list", args=[student.id]))
        assert response.status_code == 302
        student.refresh_from_db()
        assert student.is_waiting is True
        assert student.waiting_since is not None

    def test_idempotent_when_already_waiting(self, authenticated_client, waiting_student):
        original_since = waiting_student.waiting_since
        response = authenticated_client.post(reverse("add_to_waiting_list", args=[waiting_student.id]))
        assert response.status_code == 302
        waiting_student.refresh_from_db()
        assert waiting_student.is_waiting is True
        assert waiting_student.waiting_since == original_since


class TestStudentListExcludesWaiting:
    def test_waiting_students_hidden_from_students_list(
        self, authenticated_client, waiting_student, student, active_enrollment
    ):
        response = authenticated_client.get(reverse("students_list"))
        ids = {s.id for s in response.context["students"]}
        assert student.id in ids
        assert waiting_student.id not in ids


class TestDashboardWaitingWidget:
    def test_dashboard_exposes_waiting_count(self, authenticated_client, waiting_student):
        response = authenticated_client.get(reverse("home"))
        assert response.status_code == 200
        assert response.context["waiting_count"] >= 1
