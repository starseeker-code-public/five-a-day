"""Integration tests for core.views.management — gestion dashboard + all
pricing/teacher/group/enrollment admin endpoints.
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

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
    def test_returns_list(self, authenticated_client, student_with_parent, enrollment_type_monthly, site_config):
        from billing.models import Enrollment

        Enrollment.objects.filter(student=student_with_parent).delete()
        Enrollment.objects.create(
            student=student_with_parent,
            enrollment_type=enrollment_type_monthly,
            enrollment_period_start=date(2025, 9, 15),
            enrollment_period_end=date(2026, 6, 27),
            academic_year="2025-2026",
            schedule_type="full_time",
            payment_modality="monthly",
            enrollment_amount=Decimal("54.00"),
            final_amount=Decimal("54.00"),
            status="active",
            has_language_cheque=True,
            enrollment_date=date(2025, 9, 1),
        )
        response = authenticated_client.get(reverse("language_cheque_students"))
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1
