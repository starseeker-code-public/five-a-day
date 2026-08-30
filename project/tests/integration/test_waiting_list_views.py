"""Integration tests for v1.1 — Waiting List & Group Capacity views."""

from datetime import date, timedelta

import pytest
from django.urls import reverse

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
    """The button no longer promotes the entry in place — it redirects into the
    normal "Matricular" flow (parent → student), because a waiting entry has no
    parent/tutor and promoting it produced a student (and payments) with none."""

    def test_redirects_to_parent_creation(self, authenticated_client, waiting_student):
        response = authenticated_client.get(reverse("assign_from_waiting_list", args=[waiting_student.id]))
        assert response.status_code == 302
        assert response.url == f"{reverse('parent_create')}?from_waiting={waiting_student.id}"

    def test_does_not_promote_or_enroll(self, authenticated_client, waiting_student, site_config):
        from billing.models import Payment

        authenticated_client.get(reverse("assign_from_waiting_list", args=[waiting_student.id]))
        waiting_student.refresh_from_db()
        assert waiting_student.is_waiting is True
        assert not waiting_student.enrollments.exists()
        assert not Payment.objects.filter(student=waiting_student).exists()

    def test_rejects_when_group_full(self, authenticated_client, waiting_student, group, student):
        group.max_students = 1  # `student` already fills it
        group.save()
        response = authenticated_client.get(reverse("assign_from_waiting_list", args=[waiting_student.id]))
        assert response.status_code == 302
        assert response.url == reverse("waiting_list")
        waiting_student.refresh_from_db()
        assert waiting_student.is_waiting is True  # Still waiting

    def test_404_when_not_waiting(self, authenticated_client, student):
        response = authenticated_client.get(reverse("assign_from_waiting_list", args=[student.id]))
        assert response.status_code == 404


class TestWaitingPrefill:
    def test_parent_form_prefills_the_phone_contact(self, authenticated_client, waiting_student):
        waiting_student.waiting_contact_name = "Ana Gómez"
        waiting_student.waiting_contact_phone = "600111222"
        waiting_student.save()

        response = authenticated_client.get(reverse("parent_create"), {"from_waiting": waiting_student.id})
        initial = response.context["form"].initial
        assert initial["first_name"] == "Ana"
        assert initial["last_name"] == "Gómez"
        assert initial["phone"] == "600111222"
        assert response.context["waiting_entry"].id == waiting_student.id

    def test_student_form_prefills_from_the_waiting_entry(self, authenticated_client, waiting_student, parent):
        response = authenticated_client.get(
            reverse("student_create"),
            {"parent_id": parent.id, "from_waiting": waiting_student.id},
        )
        initial = response.context["form"].initial
        assert initial["first_name"] == waiting_student.first_name
        assert initial["last_name"] == waiting_student.last_name
        assert initial["group"] == waiting_student.group_id

    def test_bad_from_waiting_id_is_ignored(self, authenticated_client, parent):
        response = authenticated_client.get(reverse("parent_create"), {"from_waiting": "abc"})
        assert response.status_code == 200
        assert response.context["waiting_entry"] is None


class TestWaitingEntryIsDiscardedOnEnrollment:
    """Saving the real student must clear the placeholder from the list."""

    def _post_student(self, client, parent, group, waiting):
        return client.post(
            reverse("student_create"),
            {
                "first_name": waiting.first_name,
                "last_name": waiting.last_name,
                "birth_date": "2018-01-01",
                "school": "S",
                "allergies": "",
                "gdpr_signed": "on",
                "group": group.id,
                "parent_id": parent.id,
                "enrollment_plan": "monthly_full",
                "from_waiting": waiting.id,
            },
        )

    def test_entry_is_deleted_and_the_new_student_is_enrolled(
        self, authenticated_client, waiting_student, parent, group, site_config, enrollment_type_new_student
    ):
        response = self._post_student(authenticated_client, parent, group, waiting_student)
        assert response.status_code == 302
        assert not Student.objects.filter(id=waiting_student.id).exists()

        created = Student.objects.get(first_name=waiting_student.first_name, is_waiting=False)
        assert created.parents.filter(id=parent.id).exists()
        assert created.enrollments.filter(status="active").exists()

    def test_entry_with_payment_history_is_archived_instead(
        self, authenticated_client, waiting_student, parent, group, site_config, enrollment_type_new_student
    ):
        """A student moved *back* onto the list keeps PROTECTed payments, so the
        placeholder is archived rather than deleted — the enrollment must still
        go through."""
        from datetime import date

        from billing.models import Payment

        Payment.objects.create(
            student=waiting_student,
            parent=parent,
            payment_type="monthly",
            payment_method="transfer",
            amount=100,
            currency="EUR",
            payment_status="pending",
            due_date=date(2026, 1, 31),
            concept="Histórico",
        )

        response = self._post_student(authenticated_client, parent, group, waiting_student)
        assert response.status_code == 302
        waiting_student.refresh_from_db()
        assert waiting_student.active is False
        assert waiting_student.is_waiting is False

        response = authenticated_client.get(reverse("waiting_list"))
        assert waiting_student.id not in {s.id for s in response.context["waiting_students"]}


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


class TestWaitingListCreateForm:
    """v1.17.4 — the phone form drops the surname and gains a priority flag."""

    URL_NAME = "waiting_list_create"

    def _post(self, client, **overrides):
        data = {"first_name": "Lucia", "waiting_contact_phone": "600111222"}
        data.update(overrides)
        return client.post(reverse(self.URL_NAME), data)

    def test_form_page_loads(self, authenticated_client):
        response = authenticated_client.get(reverse(self.URL_NAME))
        assert response.status_code == 200

    def test_surname_is_not_asked_for(self, authenticated_client):
        response = authenticated_client.get(reverse(self.URL_NAME))
        assert "last_name" not in response.context["form"].fields

    def test_creates_an_entry_without_a_surname(self, authenticated_client):
        response = self._post(authenticated_client)

        assert response.status_code == 302
        entry = Student.objects.get(first_name="Lucia")
        assert entry.is_waiting is True
        assert entry.last_name == ""
        # No trailing space from the empty surname — this string is the link text
        # in the list and the subject of the history entry.
        assert entry.full_name == "Lucia"

    def test_priority_defaults_to_off(self, authenticated_client):
        self._post(authenticated_client)
        assert Student.objects.get(first_name="Lucia").waiting_priority is False

    def test_priority_checkbox_is_saved(self, authenticated_client):
        self._post(authenticated_client, waiting_priority="on")
        assert Student.objects.get(first_name="Lucia").waiting_priority is True

    def test_history_entry_marks_a_priority_signup(self, authenticated_client):
        from core.models import HistoryLog

        self._post(authenticated_client, waiting_priority="on")

        assert HistoryLog.objects.filter(action="waiting_list_added", message__contains="(prioritario)").exists()


class TestWaitingListPriorityOrdering:
    def test_priority_entries_come_first(self, authenticated_client, group):
        from django.utils import timezone

        older = Student.objects.create(first_name="Primero", group=group, active=True, is_waiting=True)
        priority = Student.objects.create(
            first_name="Urgente", group=group, active=True, is_waiting=True, waiting_priority=True
        )
        # `waiting_since` is auto-set on save, so pin it: the priority entry has to
        # win despite having waited strictly less than the other.
        Student.objects.filter(pk=older.pk).update(waiting_since=timezone.now() - timedelta(days=30))

        response = authenticated_client.get(reverse("waiting_list"))
        order = [s.id for s in response.context["waiting_students"]]

        assert order.index(priority.id) < order.index(older.id)

    def test_fifo_is_kept_within_each_band(self, authenticated_client, group):
        from django.utils import timezone

        first = Student.objects.create(
            first_name="Prio1", group=group, active=True, is_waiting=True, waiting_priority=True
        )
        second = Student.objects.create(
            first_name="Prio2", group=group, active=True, is_waiting=True, waiting_priority=True
        )
        Student.objects.filter(pk=first.pk).update(waiting_since=timezone.now() - timedelta(days=10))

        response = authenticated_client.get(reverse("waiting_list"))
        order = [s.id for s in response.context["waiting_students"]]

        assert order.index(first.id) < order.index(second.id)


class TestFullStudentFormStillNeedsASurname:
    """`Student.last_name` became blank=True for the phone form; the real ficha
    must not inherit that."""

    def test_last_name_is_required(self):
        from students.forms import StudentForm

        assert StudentForm().fields["last_name"].required is True
