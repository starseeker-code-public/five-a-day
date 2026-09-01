"""
Tests for the password-reset flow.

Covers the four standalone URLs (form / done / confirm / complete), verifies
they are public (no login required), and confirms the custom EmailService
integration delivers the reset email.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from students.models import Teacher

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def teacher_with_user():
    teacher = Teacher.objects.create(
        first_name="Reset",
        last_name="Tester",
        email="reset.me@example.com",
        admin=False,
    )
    teacher.ensure_user(password="before-reset")
    return teacher


class TestPasswordResetPublicAccess:
    """All four URLs must be reachable without authentication."""

    def test_form_page_public(self, client):
        response = client.get(reverse("password_reset"))
        assert response.status_code == 200

    def test_done_page_public(self, client):
        response = client.get(reverse("password_reset_done"))
        assert response.status_code == 200

    def test_confirm_page_public(self, client):
        """Confirm page is public — token/uid are their own gate."""
        # Use a dummy uid/token — the page still renders (shows 'invalid link')
        response = client.get(reverse("password_reset_confirm", kwargs={"uidb64": "MQ", "token": "bad-token"}))
        assert response.status_code == 200

    def test_complete_page_public(self, client):
        response = client.get(reverse("password_reset_complete"))
        assert response.status_code == 200


class TestPasswordResetFlow:
    def test_submit_email_sends_reset_mail(self, client, teacher_with_user):
        """POSTing a valid email sends a reset email (via settings.EMAIL_BACKEND = locmem in tests)."""
        mail.outbox.clear()
        response = client.post(
            reverse("password_reset"),
            {"email": "reset.me@example.com"},
        )
        assert response.status_code == 302
        assert response.url == reverse("password_reset_done")
        assert len(mail.outbox) == 1

        msg = mail.outbox[0]
        assert msg.to == ["reset.me@example.com"]
        assert "Five a Day" in msg.subject
        # Plain-text body contains the reset link
        assert "/password-reset/confirm/" in msg.body
        # HTML alternative is attached (from emails/password_reset.html)
        html_alt = next((c for c in msg.alternatives if c[1] == "text/html"), None)
        assert html_alt is not None
        assert "Restablecer mi contraseña" in html_alt[0]

    def test_unknown_email_still_redirects_done(self, client):
        """Django deliberately doesn't reveal whether an email exists — still → done, no mail."""
        mail.outbox.clear()
        response = client.post(
            reverse("password_reset"),
            {"email": "nobody@example.com"},
        )
        assert response.status_code == 302
        assert response.url == reverse("password_reset_done")
        assert len(mail.outbox) == 0

    def test_valid_confirm_token_allows_setting_password(self, client, teacher_with_user):
        user = teacher_with_user.user
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        url = reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token})

        # Django's PasswordResetConfirmView uses an internal "set-password" redirect
        # on first visit. Follow the redirect.
        response = client.get(url, follow=True)
        assert response.status_code == 200

        # The redirect URL embeds "set-password" as the token; POST the new password there.
        final_url = response.redirect_chain[-1][0] if response.redirect_chain else url
        response = client.post(
            final_url,
            {"new_password1": "brand-new-pw-456", "new_password2": "brand-new-pw-456"},
        )
        assert response.status_code == 302
        assert response.url == reverse("password_reset_complete")

        user.refresh_from_db()
        assert user.check_password("brand-new-pw-456") is True

    def test_invalid_token_shows_invalid_link(self, client, teacher_with_user):
        user = teacher_with_user.user
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        response = client.get(
            reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": "not-valid"}),
        )
        assert response.status_code == 200
        # Our branded template shows 'Enlace inválido' on the invalid-link branch.
        body = response.content.decode("utf-8")
        assert "Enlace inválido" in body or "caducado" in body
