"""Integration tests for the parent portal (v1.9)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from students.models import ParentSessionToken

pytestmark = pytest.mark.django_db


def _login_as(client, parent):
    session = client.session
    session["parent_id"] = parent.id
    session.save()
    return client


class TestMagicLinkFlow:
    def test_login_page_loads(self, client):
        response = client.get(reverse("parent_portal_login"))
        assert response.status_code == 200

    def test_login_post_unknown_email_shows_generic_success(self, client):
        response = client.post(reverse("parent_portal_login"), {"email": "nobody@example.com"})
        assert response.status_code == 200
        assert b"Revisa tu email" in response.content

    def test_login_post_issues_token_for_known_parent(self, client, parent):
        before = ParentSessionToken.objects.count()
        response = client.post(reverse("parent_portal_login"), {"email": parent.email})
        assert response.status_code == 200
        assert ParentSessionToken.objects.count() == before + 1

    def test_verify_valid_token_sets_session(self, client, parent):
        token = ParentSessionToken.issue(parent)
        response = client.get(reverse("parent_portal_verify", args=[token.token]))
        assert response.status_code == 302
        assert client.session.get("parent_id") == parent.id
        token.refresh_from_db()
        assert token.used_at is not None

    def test_verify_expired_token_rejected(self, client, parent):
        token = ParentSessionToken.issue(parent)
        token.expires_at = timezone.now() - timedelta(minutes=1)
        token.save(update_fields=["expires_at"])
        response = client.get(reverse("parent_portal_verify", args=[token.token]))
        assert response.status_code == 302
        assert response.url == reverse("parent_portal_login")
        assert "parent_id" not in client.session

    def test_verify_reused_token_rejected(self, client, parent):
        token = ParentSessionToken.issue(parent)
        token.consume()
        response = client.get(reverse("parent_portal_verify", args=[token.token]))
        assert "parent_id" not in client.session
        assert response.status_code == 302

    def test_verify_unknown_token_rejected(self, client):
        response = client.get(reverse("parent_portal_verify", args=["nonexistent"]))
        assert response.status_code == 302
        assert response.url == reverse("parent_portal_login")


class TestPortalPages:
    def test_dashboard_requires_login(self, client):
        response = client.get(reverse("parent_portal_dashboard"))
        assert response.status_code == 302
        assert response.url == reverse("parent_portal_login")

    def test_dashboard_loads_for_logged_in_parent(self, client, parent):
        _login_as(client, parent)
        response = client.get(reverse("parent_portal_dashboard"))
        assert response.status_code == 200
        assert response.context["parent"] == parent

    def test_payments_history(self, client, parent, pending_payment):
        _login_as(client, parent)
        response = client.get(reverse("parent_portal_payments"), {"year": pending_payment.due_date.year})
        assert response.status_code == 200
        assert pending_payment in response.context["payments"]

    def test_receipt_only_for_own_payment(self, client, parent, second_parent, pending_payment):
        # Parent A owns pending_payment; second_parent must not be able to view it.
        _login_as(client, second_parent)
        response = client.get(reverse("parent_portal_receipt", args=[pending_payment.id]))
        assert response.status_code == 404

    def test_receipt_returns_pdf(self, client, parent, pending_payment):
        _login_as(client, parent)
        response = client.get(reverse("parent_portal_receipt", args=[pending_payment.id]))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"

    def test_tax_certificate(self, client, parent):
        _login_as(client, parent)
        response = client.get(reverse("parent_portal_tax_certificate"))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"

    def test_logout_clears_session(self, client, parent):
        _login_as(client, parent)
        response = client.get(reverse("parent_portal_logout"))
        assert response.status_code == 302
        assert "parent_id" not in client.session
