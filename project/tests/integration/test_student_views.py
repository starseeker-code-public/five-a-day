"""Tests for core.views.students — list, detail, create, update, search."""

from unittest.mock import patch

import pytest
from django.urls import reverse

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
    def test_get_renders_form(self, authenticated_client, group, site_config, enrollment_type_monthly):
        response = authenticated_client.get(reverse("student_create"))
        assert response.status_code == 200
        assert "enrollment_form" in response.context

    def test_success_page(self, authenticated_client, group, site_config, enrollment_type_monthly):
        url = reverse("student_create") + "?success=1&student_name=Test&fee=40"
        response = authenticated_client.get(url)
        assert response.status_code == 200
        assert response.context["show_success"] is True

    def test_adult_mode_context(self, authenticated_client, group, site_config, enrollment_type_monthly):
        response = authenticated_client.get(reverse("student_create") + "?mode=adult")
        assert response.status_code == 200
        assert response.context["is_adult_mode"] is True


class TestStudentCreateViewPost:
    def test_creates_student_with_enrollment(
        self, authenticated_client, parent, group, site_config, enrollment_type_monthly
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

    def test_post_without_parent_id_fails(self, authenticated_client, group, enrollment_type_monthly, site_config):
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

    def test_post_with_invalid_parent_id(self, authenticated_client, group, enrollment_type_monthly, site_config):
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
        self, authenticated_client, parent, group, enrollment_type_monthly, site_config
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
        self, authenticated_client, parent, group, enrollment_type_monthly, site_config
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
