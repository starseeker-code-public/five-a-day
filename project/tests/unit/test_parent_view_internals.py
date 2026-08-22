"""Unit tests for core.views.parents view-layer internals.

`ParentCreateView.form_valid` has a branch that redirects to an already-existing
parent instead of creating a duplicate. It is unreachable through the HTTP stack:
`Parent.dni` is `unique=True` and `ParentForm` is a plain ModelForm, so Django's
`validate_unique` rejects a duplicate DNI during `form.is_valid()` and
`form_valid` is never entered. Reaching it needs a form instance whose
`cleaned_data` carries the duplicate — hence RequestFactory rather than the test
client, the same approach `test_student_view_internals.py` takes.

Full-stack ParentCreateView tests live in integration/test_parent_views.py.
"""

import pytest
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from core.views.parents import ParentCreateView
from students.forms import ParentForm
from students.models import Parent

pytestmark = pytest.mark.django_db


def _request_with_messages():
    """A POST request carrying the session + message storage that
    `messages.info()` / `messages.error()` require."""
    request = RequestFactory().post("/parents/create/")
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    MessageMiddleware(lambda r: None).process_request(request)
    return request


def _bound_view(request):
    view = ParentCreateView()
    view.request = request
    return view


class TestFormValidExistingDni:
    def test_redirects_to_existing_parent_without_creating_a_duplicate(self):
        existing = Parent.objects.create(
            first_name="Ana",
            last_name="Garcia",
            dni="12345678A",
            phone="600111222",
            email="ana@example.com",
        )
        before = Parent.objects.count()

        form = ParentForm(
            data={
                "first_name": "Ana",
                "last_name": "Garcia",
                "dni": "12345678A",
                "phone": "600111222",
                "email": "ana@example.com",
                "iban": "",
            }
        )
        # The duplicate DNI makes the form invalid, which is exactly why this
        # branch cannot be reached over HTTP. Populate cleaned_data directly so
        # form_valid sees the state it is written to defend against.
        form.is_valid()
        form.cleaned_data = {"dni": "12345678A"}

        view = _bound_view(_request_with_messages())
        response = view.form_valid(form)

        assert response.status_code == 302
        assert response.url == f"/students/create/?parent_id={existing.id}"
        assert view.object == existing
        assert Parent.objects.count() == before, "must reuse the existing parent, not create another"

    def test_success_url_points_at_student_create_with_the_existing_parent(self):
        existing = Parent.objects.create(
            first_name="Luis",
            last_name="Perez",
            dni="87654321B",
            phone="600333444",
            email="luis@example.com",
        )
        view = _bound_view(_request_with_messages())
        view.object = existing
        assert view.get_success_url() == f"/students/create/?parent_id={existing.id}"
