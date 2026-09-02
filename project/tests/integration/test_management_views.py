"""Integration tests for core.views.management — gestion dashboard + all
pricing/teacher/group/enrollment admin endpoints.
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from conftest import current_course_year
from students.models import Teacher

pytestmark = pytest.mark.django_db


class TestGestionView:
    def test_renders(self, authenticated_client, teacher, group, site_config):
        response = authenticated_client.get(reverse("management"))
        assert response.status_code == 200


class TestUpdateSiteConfig:
    def test_success_updates_all_fields(self, authenticated_client):
        payload = {
            "children_enrollment_fee": "40",
            "adult_enrollment_fee": "45",
            "full_time_monthly_fee": "55",
            "part_time_monthly_fee": "37",
            "adult_group_monthly_fee": "62",
            "language_cheque_discount": "8",
            "quarterly_enrollment_discount": "6",
            "old_student_discount": "5",
            "june_discount": "50",
            "full_year_bonus": "25",
            "sibling_discount": "20",
            "half_month_discount": "50",
            "one_week_discount": "80",
            "three_week_discount": "30",
        }
        response = authenticated_client.post(
            reverse("update_site_config"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_invalid_json_returns_400(self, authenticated_client):
        response = authenticated_client.post(
            reverse("update_site_config"),
            data="not-json",
            content_type="application/json",
        )
        assert response.status_code == 400


class TestCreateTeacher:
    def test_success(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_teacher"),
            data=json.dumps(
                {
                    "first_name": "New",
                    "last_name": "Teacher",
                    "email": "newt@test.com",
                    "phone": "600000000",
                }
            ),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_missing_field(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_teacher"),
            data=json.dumps({"first_name": "N", "last_name": ""}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_duplicate_email_rejected(self, authenticated_client, teacher):
        response = authenticated_client.post(
            reverse("create_teacher"),
            data=json.dumps({"first_name": "Dup", "last_name": "T", "email": teacher.email}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_invalid_json_returns_400(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_teacher"),
            data="not-json",
            content_type="application/json",
        )
        assert response.status_code == 400


class TestCreateGroup:
    def test_success(self, authenticated_client, teacher):
        response = authenticated_client.post(
            reverse("create_group"),
            data=json.dumps({"group_name": "Brand New", "teacher_id": teacher.id, "color": "#ff0"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_missing_group_name(self, authenticated_client, teacher):
        response = authenticated_client.post(
            reverse("create_group"),
            data=json.dumps({"group_name": "", "teacher_id": teacher.id}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_missing_teacher_id(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_group"),
            data=json.dumps({"group_name": "G"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_duplicate_group_name(self, authenticated_client, group, teacher):
        response = authenticated_client.post(
            reverse("create_group"),
            data=json.dumps({"group_name": group.group_name, "teacher_id": teacher.id}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_nonexistent_teacher(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_group"),
            data=json.dumps({"group_name": "New", "teacher_id": 99999}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_invalid_json(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_group"),
            data="oops",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_max_students_defaults_to_eight(self, authenticated_client, teacher):
        from students.models import Group

        authenticated_client.post(
            reverse("create_group"),
            data=json.dumps({"group_name": "Default Cupo", "teacher_id": teacher.id}),
            content_type="application/json",
        )
        assert Group.objects.get(group_name="Default Cupo").max_students == 8

    def test_max_students_is_stored(self, authenticated_client, teacher):
        from students.models import Group

        response = authenticated_client.post(
            reverse("create_group"),
            data=json.dumps({"group_name": "Cupo 12", "teacher_id": teacher.id, "max_students": 12}),
            content_type="application/json",
        )
        assert response.json()["group"]["max_students"] == 12
        assert Group.objects.get(group_name="Cupo 12").max_students == 12

    def test_max_students_zero_means_no_cap(self, authenticated_client, teacher):
        from students.models import Group

        authenticated_client.post(
            reverse("create_group"),
            data=json.dumps({"group_name": "Sin Cupo", "teacher_id": teacher.id, "max_students": 0}),
            content_type="application/json",
        )
        group = Group.objects.get(group_name="Sin Cupo")
        assert group.max_students == 0
        assert group.available_spots is None

    def test_max_students_rejects_garbage(self, authenticated_client, teacher):
        response = authenticated_client.post(
            reverse("create_group"),
            data=json.dumps({"group_name": "Bad Cupo", "teacher_id": teacher.id, "max_students": "ocho"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["success"] is False

    def test_max_students_rejects_negative(self, authenticated_client, teacher):
        response = authenticated_client.post(
            reverse("create_group"),
            data=json.dumps({"group_name": "Neg Cupo", "teacher_id": teacher.id, "max_students": -3}),
            content_type="application/json",
        )
        assert response.status_code == 400


class TestApiGetTeachers:
    def test_returns_active_teachers(self, authenticated_client, teacher):
        response = authenticated_client.get(reverse("api_get_teachers"))
        assert response.status_code == 200
        data = response.json()
        assert any(t["id"] == teacher.id for t in data["teachers"])


class TestUpdateEnrollmentModality:
    def test_success_changes_modality(self, authenticated_client, student_with_parent, active_enrollment):
        response = authenticated_client.post(
            reverse("update_enrollment_modality", kwargs={"student_id": student_with_parent.id}),
            data=json.dumps({"payment_modality": "quarterly"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_invalid_modality(self, authenticated_client, student_with_parent, active_enrollment):
        response = authenticated_client.post(
            reverse("update_enrollment_modality", kwargs={"student_id": student_with_parent.id}),
            data=json.dumps({"payment_modality": "never"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_no_active_enrollment(self, authenticated_client, student_with_parent):
        response = authenticated_client.post(
            reverse("update_enrollment_modality", kwargs={"student_id": student_with_parent.id}),
            data=json.dumps({"payment_modality": "monthly"}),
            content_type="application/json",
        )
        assert response.status_code == 404

    def test_student_not_found(self, authenticated_client):
        response = authenticated_client.post(
            reverse("update_enrollment_modality", kwargs={"student_id": 99999}),
            data=json.dumps({"payment_modality": "monthly"}),
            content_type="application/json",
        )
        assert response.status_code == 500


class TestLanguageChequeStudents:
    def test_returns_list(self, authenticated_client, student_with_parent, enrollment_type_new_student, site_config):
        from billing.models import Enrollment

        # The endpoint filters on relevant_academic_years(), so this enrollment
        # has to be in the *current* course — see conftest.current_course_year().
        academic_year, start_year = current_course_year()

        Enrollment.objects.filter(student=student_with_parent).delete()
        Enrollment.objects.create(
            student=student_with_parent,
            enrollment_type=enrollment_type_new_student,
            enrollment_period_start=date(start_year, 9, 15),
            enrollment_period_end=date(start_year + 1, 6, 27),
            academic_year=academic_year,
            schedule_type="full_time",
            payment_modality="monthly",
            enrollment_amount=Decimal("54.00"),
            final_amount=Decimal("54.00"),
            status="active",
            has_language_cheque=True,
            enrollment_date=date(start_year, 9, 1),
        )
        response = authenticated_client.get(reverse("language_cheque_students"))
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1


class TestCreateTeacherValidatesInput:
    """`Teacher.objects.create()` runs no validators — the documented trap — so
    an address that is not an email persisted into an `EmailField`, and the
    account it produced was unreachable: `ensure_user()` mirrors the address
    onto the linked `auth.User`, and activation happens over `/password-reset/`,
    which emails it.
    """

    def _create(self, client, **overrides):
        payload = {"first_name": "Nuria", "last_name": "Vega", "email": "nuria@example.com"}
        payload.update(overrides)
        return client.post(reverse("create_teacher"), data=json.dumps(payload), content_type="application/json")

    def test_an_invalid_address_is_refused_with_a_usable_message(self, authenticated_client):
        response = self._create(authenticated_client, email="notanemail")

        assert response.status_code == 400
        body = json.loads(response.content)
        assert body["success"] is False
        # Django's own field text, not the catch-all's fixed string.
        assert "No se pudo crear el profesor" not in body["message"]
        assert not Teacher.objects.filter(email="notanemail").exists()

    def test_a_valid_teacher_still_gets_a_login_awaiting_activation(self, authenticated_client):
        assert self._create(authenticated_client).status_code == 200

        teacher = Teacher.objects.get(email="nuria@example.com")
        assert teacher.admin is False
        assert teacher.user is not None
        assert teacher.user.has_usable_password() is False

    def test_the_duplicate_check_still_runs_before_validation(self, authenticated_client, teacher):
        response = self._create(authenticated_client, email=teacher.email)

        assert response.status_code == 400
        assert "Ya existe" in json.loads(response.content)["message"]
