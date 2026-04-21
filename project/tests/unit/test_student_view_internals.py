"""Unit tests for core.views.students view-layer internals.

These classes call view-object methods (StudentUpdateView) or unreferenced
helper functions (handle_student_form, student_detail, update_student)
directly via RequestFactory — they do not go through the URL resolver
or middleware. The HTTP-based StudentCreateView/StudentListView/
StudentDetailView tests live in integration/test_student_views.py.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import RequestFactory

pytestmark = pytest.mark.django_db


class TestStudentUpdateView:
    """StudentUpdateView is exercised via RequestFactory + mocked render so we
    don't depend on the missing 'student_update.html' template. The goal is
    coverage of the view's Python branches (get_context_data / form_valid
    / form_invalid), not the HTML rendering."""

    def _req(self, method, path="/x", data=None):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        rf = RequestFactory()
        req = getattr(rf, method)(path, data=data or {})
        req.session = {"is_authenticated": True, "username": "testuser"}
        req._messages = FallbackStorage(req)
        return req

    def test_get_context_with_active_enrollment(self, student_with_parent, active_enrollment):
        from core.views.students import StudentUpdateView

        view = StudentUpdateView()
        view.request = self._req("get")
        view.kwargs = {"student_id": student_with_parent.id}
        view.object = student_with_parent
        ctx = view.get_context_data()
        assert "enrollment_form" in ctx
        assert "parents" in ctx

    def test_get_context_quarterly_enrollment(self, student_with_parent, enrollment_type_quarterly, site_config):
        from billing.models import Enrollment
        from core.views.students import StudentUpdateView

        Enrollment.objects.filter(student=student_with_parent).delete()
        Enrollment.objects.create(
            student=student_with_parent,
            enrollment_type=enrollment_type_quarterly,
            enrollment_period_start=date(2025, 9, 15),
            enrollment_period_end=date(2026, 6, 27),
            academic_year="2025-2026",
            schedule_type="full_time",
            payment_modality="quarterly",
            enrollment_amount=Decimal("153.90"),
            final_amount=Decimal("153.90"),
            status="active",
            enrollment_date=date(2025, 9, 1),
        )
        view = StudentUpdateView()
        view.request = self._req("get")
        view.object = student_with_parent
        ctx = view.get_context_data()
        assert "enrollment_form" in ctx

    def test_get_context_part_time(self, student_with_parent, enrollment_type_monthly, site_config):
        from billing.models import Enrollment
        from core.views.students import StudentUpdateView

        Enrollment.objects.filter(student=student_with_parent).delete()
        Enrollment.objects.create(
            student=student_with_parent,
            enrollment_type=enrollment_type_monthly,
            enrollment_period_start=date(2025, 9, 15),
            enrollment_period_end=date(2026, 6, 27),
            academic_year="2025-2026",
            schedule_type="part_time",
            payment_modality="monthly",
            enrollment_amount=Decimal("36.00"),
            final_amount=Decimal("36.00"),
            status="active",
            enrollment_date=date(2025, 9, 1),
        )
        view = StudentUpdateView()
        view.request = self._req("get")
        view.object = student_with_parent
        ctx = view.get_context_data()
        assert "enrollment_form" in ctx

    def test_get_context_no_active_enrollment(self, student_with_parent):
        from core.views.students import StudentUpdateView

        view = StudentUpdateView()
        view.request = self._req("get")
        view.object = student_with_parent
        ctx = view.get_context_data()
        assert "enrollment_form" in ctx

    def test_success_url(self, student_with_parent):
        from core.views.students import StudentUpdateView

        view = StudentUpdateView()
        view.object = student_with_parent
        assert "/students/" in str(view.get_success_url())

    def test_form_valid_invalid_enrollment_returns_form_invalid(self, student_with_parent, active_enrollment):
        """Invalid enrollment form → form_invalid path. Mock render to avoid missing template."""
        from core.views.students import StudentUpdateView
        from students.forms import StudentForm

        view = StudentUpdateView()
        view.request = self._req(
            "post",
            data={
                "first_name": student_with_parent.first_name,
                "last_name": student_with_parent.last_name,
                "birth_date": student_with_parent.birth_date.isoformat(),
                "group": str(student_with_parent.group_id),
                "gdpr_signed": "on",
                "active": "on",
                # no enrollment_plan → EnrollmentForm will be invalid
            },
        )
        view.object = student_with_parent
        view.kwargs = {"student_id": student_with_parent.id}
        form = StudentForm(view.request.POST, instance=student_with_parent)
        form.is_valid()
        with patch.object(view, "render_to_response", return_value="ok"):
            result = view.form_valid(form)
        assert result is not None

    def test_form_valid_exception_goes_to_form_invalid(self, student_with_parent, active_enrollment):
        from core.views.students import StudentUpdateView
        from students.forms import StudentForm

        view = StudentUpdateView()
        view.request = self._req(
            "post",
            data={
                "first_name": student_with_parent.first_name,
                "last_name": student_with_parent.last_name,
                "birth_date": student_with_parent.birth_date.isoformat(),
                "group": str(student_with_parent.group_id),
                "gdpr_signed": "on",
                "active": "on",
                "enrollment_plan": "monthly_full",
                "discount": "0",
            },
        )
        view.object = student_with_parent
        view.kwargs = {"student_id": student_with_parent.id}
        form = StudentForm(view.request.POST, instance=student_with_parent)
        form.is_valid()
        with (
            patch("billing.forms.EnrollmentForm.create_enrollment", side_effect=Exception("db")),
            patch.object(view, "render_to_response", return_value="ok"),
        ):
            result = view.form_valid(form)
        assert result is not None


# ============================================================================
# handle_student_form, student_detail, update_student — unreferenced helper
# functions called directly via RequestFactory. They have no URL pattern, so
# we construct the request ourselves.
# ============================================================================


@pytest.fixture
def rf():
    return RequestFactory()


def _auth_post(rf, path, data):
    req = rf.post(path, data=data)
    # Add a message storage so messages.error(...) doesn't blow up
    from django.contrib.messages.storage.fallback import FallbackStorage

    req.session = {}
    req._messages = FallbackStorage(req)
    return req


def _auth_get(rf, path):
    req = rf.get(path)
    from django.contrib.messages.storage.fallback import FallbackStorage

    req.session = {}
    req._messages = FallbackStorage(req)
    return req


class TestHandleStudentForm:
    def test_update_success(self, rf, student_with_parent, group, parent):
        from core.views.students import handle_student_form

        req = _auth_post(
            rf,
            "/update/",
            {
                "first_name": "Renamed",
                "last_name": "Child",
                "birth_date": "2018-05-15",
                "group": str(group.id),
                "parents": [str(parent.id)],
                "gdpr_signed": "on",
                "active": "on",
                "email": "kid@example.com",
                "school": "Test School",
                "allergies": "None",
                "student_id": str(student_with_parent.id),
            },
        )
        response = handle_student_form(req)
        assert response.status_code == 302

    def test_create_success(self, rf, group, parent):
        from core.views.students import handle_student_form

        req = _auth_post(
            rf,
            "/create/",
            {
                "first_name": "New",
                "last_name": "Kid",
                "birth_date": "2018-05-15",
                "group": str(group.id),
                "parents": [str(parent.id)],
                "gdpr_signed": "on",
                "active": "on",
            },
        )
        response = handle_student_form(req)
        assert response.status_code == 302

    def test_missing_name_fails(self, rf, group, parent):
        from core.views.students import handle_student_form

        req = _auth_post(
            rf,
            "/create/",
            {
                "first_name": "",
                "last_name": "",
                "birth_date": "2018-05-15",
                "group": str(group.id),
                "parents": [str(parent.id)],
            },
        )
        response = handle_student_form(req)
        assert response.status_code == 302

    def test_missing_birth_date_fails(self, rf, group, parent):
        from core.views.students import handle_student_form

        req = _auth_post(
            rf,
            "/create/",
            {
                "first_name": "K",
                "last_name": "X",
                "birth_date": "",
                "group": str(group.id),
                "parents": [str(parent.id)],
            },
        )
        response = handle_student_form(req)
        assert response.status_code == 302

    def test_missing_group_fails(self, rf, parent):
        from core.views.students import handle_student_form

        req = _auth_post(
            rf,
            "/create/",
            {
                "first_name": "K",
                "last_name": "X",
                "birth_date": "2018-01-01",
                "group": "",
                "parents": [str(parent.id)],
            },
        )
        response = handle_student_form(req)
        assert response.status_code == 302

    def test_missing_parents_fails(self, rf, group):
        from core.views.students import handle_student_form

        req = _auth_post(
            rf,
            "/create/",
            {
                "first_name": "K",
                "last_name": "X",
                "birth_date": "2018-01-01",
                "group": str(group.id),
                "parents": [],
            },
        )
        response = handle_student_form(req)
        assert response.status_code == 302

    def test_invalid_group(self, rf, parent):
        from core.views.students import handle_student_form

        req = _auth_post(
            rf,
            "/create/",
            {
                "first_name": "K",
                "last_name": "X",
                "birth_date": "2018-01-01",
                "group": "99999",
                "parents": [str(parent.id)],
            },
        )
        response = handle_student_form(req)
        assert response.status_code == 302

    def test_invalid_parent_ids(self, rf, group):
        from core.views.students import handle_student_form

        req = _auth_post(
            rf,
            "/create/",
            {
                "first_name": "K",
                "last_name": "X",
                "birth_date": "2018-01-01",
                "group": str(group.id),
                "parents": ["99999"],
            },
        )
        response = handle_student_form(req)
        assert response.status_code == 302

    def test_update_group_change_logs_history(self, rf, student_with_parent, parent, teacher):
        from core.models import HistoryLog
        from core.views.students import handle_student_form
        from students.models import Group

        new_group = Group.objects.create(group_name="New Group", color="#fff", teacher=teacher, active=True)
        before = HistoryLog.objects.filter(action="group_updated").count()
        req = _auth_post(
            rf,
            "/update/",
            {
                "first_name": student_with_parent.first_name,
                "last_name": student_with_parent.last_name,
                "birth_date": "2018-05-15",
                "group": str(new_group.id),
                "parents": [str(parent.id)],
                "gdpr_signed": "on",
                "active": "on",
                "student_id": str(student_with_parent.id),
            },
        )
        response = handle_student_form(req)
        assert response.status_code == 302
        assert HistoryLog.objects.filter(action="group_updated").count() == before + 1

    def test_update_nonexistent_student(self, rf, group, parent):
        from core.views.students import handle_student_form

        req = _auth_post(
            rf,
            "/update/",
            {
                "first_name": "K",
                "last_name": "X",
                "birth_date": "2018-01-01",
                "group": str(group.id),
                "parents": [str(parent.id)],
                "student_id": "99999",
            },
        )
        response = handle_student_form(req)
        assert response.status_code == 302

    def test_validation_error_handled(self, rf, group, parent):
        """full_clean() raising ValidationError on create path."""
        from core.views.students import handle_student_form

        req = _auth_post(
            rf,
            "/create/",
            {
                "first_name": "A" * 300,  # too long → ValidationError
                "last_name": "X",
                "birth_date": "2018-01-01",
                "group": str(group.id),
                "parents": [str(parent.id)],
            },
        )
        response = handle_student_form(req)
        assert response.status_code == 302


class TestStudentDetailHelper:
    def test_get_returns_json(self, rf, student_with_parent):
        from core.views.students import student_detail

        req = _auth_get(rf, f"/api/students/{student_with_parent.id}/")
        response = student_detail(req, student_with_parent.id)
        assert response.status_code == 200

    def test_non_get_returns_405(self, rf, student_with_parent):
        from core.views.students import student_detail

        req = _auth_post(rf, "/x", {})
        response = student_detail(req, student_with_parent.id)
        assert response.status_code == 405

    def test_nonexistent_returns_500(self, rf):
        """Http404 is caught by the bare `except Exception` → 500."""
        from core.views.students import student_detail

        req = _auth_get(rf, "/api/students/99999/")
        response = student_detail(req, 99999)
        assert response.status_code == 500


class TestUpdateStudentHelper:
    def test_get_returns_json(self, rf, student_with_parent):
        from core.views.students import update_student

        req = _auth_get(rf, "/x")
        response = update_student(req, student_with_parent.id)
        assert response.status_code == 200

    def test_get_nonexistent_raises_http404(self, rf):
        """update_student's GET branch uses get_object_or_404 outside a
        try/except — Http404 propagates."""
        from django.http import Http404

        from core.views.students import update_student

        req = _auth_get(rf, "/x")
        with pytest.raises(Http404):
            update_student(req, 99999)

    def test_post_delegates_to_handle_form(self, rf, student_with_parent, group, parent):
        from core.views.students import update_student

        req = _auth_post(
            rf,
            "/x",
            {
                "first_name": student_with_parent.first_name,
                "last_name": student_with_parent.last_name,
                "birth_date": "2018-05-15",
                "group": str(group.id),
                "parents": [str(parent.id)],
            },
        )
        response = update_student(req, student_with_parent.id)
        assert response.status_code == 302

    def test_other_method_returns_405(self, rf, student_with_parent):
        from core.views.students import update_student

        req = rf.delete("/x")
        response = update_student(req, student_with_parent.id)
        assert response.status_code == 405
