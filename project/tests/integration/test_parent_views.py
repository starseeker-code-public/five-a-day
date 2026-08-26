"""Tests for core.views.parents — ParentCreateView."""

from unittest.mock import patch

import pytest
from django.urls import reverse

from students.models import Parent

pytestmark = pytest.mark.django_db


class TestParentCreateView:
    def test_get_renders_form(self, authenticated_client):
        response = authenticated_client.get(reverse("parent_create"))
        assert response.status_code == 200

    def test_post_creates_parent(self, authenticated_client):
        response = authenticated_client.post(
            reverse("parent_create"),
            {
                "first_name": "Laura",
                "last_name": "Fernández",
                "dni": "11223344X",
                "phone": "600111222",
                "email": "laura@test.com",
                "iban": "ES0011223344556677889900",
            },
        )
        assert response.status_code == 302
        assert Parent.objects.filter(dni="11223344X").exists()
        parent = Parent.objects.get(dni="11223344X")
        assert f"parent_id={parent.id}" in response.url

    def test_post_existing_dni_reuses_the_existing_parent(self, authenticated_client, parent):
        """A repeat DNI means "this family is already registered" — typically a
        second sibling. The view reuses the existing parent and sends the user
        on to create the child, rather than showing a uniqueness error.

        This path was previously unreachable: `Parent.dni` is unique, so the
        ModelForm failed validation and `form_valid()` never ran.
        """
        before = Parent.objects.count()
        response = authenticated_client.post(
            reverse("parent_create"),
            {
                "first_name": "Different",
                "last_name": "Name",
                "dni": parent.dni,  # same DNI as existing parent
                "phone": "600999888",
                "email": "different@test.com",
            },
        )
        assert response.status_code == 302
        assert f"parent_id={parent.id}" in response.url
        assert Parent.objects.count() == before, "must not create a duplicate parent row"

    def test_post_short_dni_still_shows_form_error(self, authenticated_client):
        """Relaxing the uniqueness check must not relax the format check."""
        response = authenticated_client.post(
            reverse("parent_create"),
            {
                "first_name": "Too",
                "last_name": "Short",
                "dni": "123",
                "phone": "600999888",
                "email": "short@test.com",
            },
        )
        assert response.status_code == 200
        assert not Parent.objects.filter(dni="123").exists()

    def test_post_invalid_data_shows_form(self, authenticated_client):
        response = authenticated_client.post(
            reverse("parent_create"),
            {"first_name": "", "last_name": "", "dni": ""},
        )
        assert response.status_code == 200  # re-renders form with errors


# ============================================================================
# Extra coverage: ParentCreateView with mocked dependencies + edge paths
# ============================================================================


class TestParentCreateViewExtra:
    def test_get_renders(self, authenticated_client):
        response = authenticated_client.get(reverse("parent_create"))
        assert response.status_code == 200

    def test_post_create_new(self, authenticated_client):
        response = authenticated_client.post(
            reverse("parent_create"),
            {
                "first_name": "Brand",
                "last_name": "New",
                "dni": "55555555Z",
                "phone": "600555555",
                "email": "bn@test.com",
            },
        )
        assert response.status_code == 302

    def test_post_existing_dni_redirects(self, authenticated_client, parent):
        """A duplicate DNI redirects to the student form for the existing parent.

        The old docstring here described the opposite (a 200 re-render) and the
        assertion accepted either status, so it passed whichever way the view
        behaved — it could not have caught the bug where the friendly branch was
        unreachable.
        """
        response = authenticated_client.post(
            reverse("parent_create"),
            {
                "first_name": "Dup",
                "last_name": "Name",
                "dni": parent.dni,
                "phone": "600111111",
                "email": "dup@test.com",
            },
        )
        assert response.status_code == 302
        assert f"parent_id={parent.id}" in response.url

    def test_post_exception_triggers_form_invalid(self, authenticated_client):
        from students.models import Parent

        with patch.object(Parent, "save", side_effect=RuntimeError("db")):
            response = authenticated_client.post(
                reverse("parent_create"),
                {
                    "first_name": "X",
                    "last_name": "Y",
                    "dni": "66666666A",
                    "phone": "600666666",
                    "email": "x@test.com",
                },
            )
        # form_invalid returns to the same template
        assert response.status_code == 200
