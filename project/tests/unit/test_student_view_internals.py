"""Unit tests for core.views.students view-layer internals.

These call `StudentUpdateView`'s methods directly via RequestFactory, so they
do not go through the URL resolver or middleware. The HTTP-based
StudentCreateView/StudentListView/StudentDetailView tests live in
integration/test_student_views.py.
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

    def test_get_context_quarterly_enrollment(
        self, student_with_parent, enrollment_type_returning_student, site_config
    ):
        from billing.models import Enrollment
        from core.views.students import StudentUpdateView

        Enrollment.objects.filter(student=student_with_parent).delete()
        Enrollment.objects.create(
            student=student_with_parent,
            enrollment_type=enrollment_type_returning_student,
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

    def test_get_context_part_time(self, student_with_parent, enrollment_type_new_student, site_config):
        from billing.models import Enrollment
        from core.views.students import StudentUpdateView

        Enrollment.objects.filter(student=student_with_parent).delete()
        Enrollment.objects.create(
            student=student_with_parent,
            enrollment_type=enrollment_type_new_student,
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
# handle_student_form, update_student — unreferenced helper
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
