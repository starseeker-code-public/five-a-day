"""Integration tests for core.views.fun_friday_attendance — toggle/add/remove
endpoints for Fun Friday student attendance.
"""

import json
from datetime import date

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestFunFridayAttendanceEndpoints:
    def test_toggle_adult_rejected(self, authenticated_client, adult_student):
        response = authenticated_client.post(
            reverse("toggle_fun_friday_this_week", kwargs={"student_id": adult_student.id})
        )
        assert response.status_code == 400

    def test_toggle_on_then_off(self, authenticated_client, student):
        r1 = authenticated_client.post(reverse("toggle_fun_friday_this_week", kwargs={"student_id": student.id}))
        assert r1.status_code == 200 and r1.json()["is_this_week"] is True

        r2 = authenticated_client.post(reverse("toggle_fun_friday_this_week", kwargs={"student_id": student.id}))
        assert r2.status_code == 200 and r2.json()["is_this_week"] is False

    def test_add_attendance(self, authenticated_client, student):
        response = authenticated_client.post(
            reverse("add_fun_friday_attendance", kwargs={"student_id": student.id}),
            data=json.dumps({"date": "2026-05-01"}),
            content_type="application/json",
        )
        assert response.status_code == 200

    def test_add_attendance_invalid(self, authenticated_client, student):
        response = authenticated_client.post(
            reverse("add_fun_friday_attendance", kwargs={"student_id": student.id}),
            data="not-json",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_remove_attendance(self, authenticated_client, student):
        from core.models import FunFridayAttendance

        FunFridayAttendance.objects.create(student=student, date=date(2026, 5, 1))
        response = authenticated_client.post(
            reverse("remove_fun_friday_attendance", kwargs={"student_id": student.id}),
            data=json.dumps({"date": "2026-05-01"}),
            content_type="application/json",
        )
        assert response.status_code == 200

    def test_remove_attendance_invalid(self, authenticated_client, student):
        response = authenticated_client.post(
            reverse("remove_fun_friday_attendance", kwargs={"student_id": student.id}),
            data="not-json",
            content_type="application/json",
        )
        assert response.status_code == 400
