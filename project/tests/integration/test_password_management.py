"""
Integration tests for the two authenticated password paths.

Both existed only as instructions before: creating a teacher provisioned an
account with an unusable password and told the admin to relay "go to
¿Olvidaste tu contraseña?" by hand (no email was sent at all), and there was
no way for a logged-in user to change their own password anywhere in the app.
"""

import json
import re
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.urls import reverse

from students.models import Teacher

User = get_user_model()
pytestmark = pytest.mark.django_db


def _make_teacher(email="pw@fiveaday.test", password="old-password-1", admin=True):
    teacher = Teacher.objects.create(
        first_name="Pass",
        last_name="Teacher",
        email=email,
        admin=admin,
    )
    teacher.ensure_user(password=password)
    return teacher


def _logged_in(client, teacher):
    """Both auth layers: Django's (for request.user) and the session flag."""
    client.force_login(teacher.user)
    session = client.session
    session["is_authenticated"] = True
    session["username"] = teacher.first_name
    session.save()
    return client


def _confirm_link(message) -> str:
    """Pull the /password-reset/confirm/<uid>/<token>/ path out of an email."""
    match = re.search(r"/password-reset/confirm/[^/]+/[^/\s]+/", message.body)
    assert match, f"no confirm link in:\n{message.body}"
    return match.group(0)


class TestCreateTeacherSendsActivationEmail:
    def test_email_is_sent_with_a_working_link(self, authenticated_client, client):
        mail.outbox.clear()
        response = authenticated_client.post(
            reverse("create_teacher"),
            data=json.dumps({"first_name": "Nuevo", "last_name": "Profe", "email": "nuevo@fiveaday.test"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["activation_email_sent"] is True

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ["nuevo@fiveaday.test"]
        assert "Activa tu cuenta" in message.subject
        # Activation wording, not the reset copy — which tells the reader to
        # ignore the message if they didn't request it.
        assert "solicitud para restablecer" not in message.body
        # HTML alternative attached, like every other transactional email.
        assert any(content_type == "text/html" for _, content_type in message.alternatives)

        # The link works end to end: the account has an UNUSABLE password at
        # this point, which Django's stock PasswordResetForm.get_users()
        # filters out — that is what made this flow send nothing before.
        user = User.objects.get(email="nuevo@fiveaday.test")
        assert not user.has_usable_password()

        link = _confirm_link(message)
        landing = client.get(link)
        assert landing.status_code == 302
        done = client.post(
            landing.url,
            {"new_password1": "brand-new-pw-42", "new_password2": "brand-new-pw-42"},
        )
        assert done.status_code == 302
        user.refresh_from_db()
        assert user.check_password("brand-new-pw-42")

    def test_teacher_survives_a_failing_mail_send(self, authenticated_client, monkeypatch):
        """The Teacher row is committed before the send, so a dead SMTP hop
        must not lose the account — it degrades to the manual reset flow."""
        import core.views.password_reset as pr

        def boom(*args, **kwargs):
            raise OSError("smtp down")

        monkeypatch.setattr(pr.ActivationFriendlyPasswordResetForm, "save", boom)

        response = authenticated_client.post(
            reverse("create_teacher"),
            data=json.dumps({"first_name": "Sin", "last_name": "Correo", "email": "sincorreo@fiveaday.test"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["activation_email_sent"] is False
        assert Teacher.objects.filter(email="sincorreo@fiveaday.test").exists()


class TestChangePasswordButtonVisibility:
    def test_shown_for_a_password_account(self, client):
        teacher = _make_teacher()
        response = _logged_in(client, teacher).get(reverse("management"))
        assert response.context["can_change_password"] is True
        assert b'id="btn-change-password"' in response.content

    def test_hidden_for_a_google_oauth_session(self, client):
        teacher = _make_teacher(email="oauth@fiveaday.test")
        _logged_in(client, teacher)
        session = client.session
        session["google_authenticated"] = True
        session.save()

        response = client.get(reverse("management"))
        # Google owns that identity — and note this account DOES carry a usable
        # password (OAuth links to a Teacher by email), so the session flag is
        # what has to gate the button, not the password state.
        assert response.context["can_change_password"] is False
        assert b'id="btn-change-password"' not in response.content

    def test_hidden_for_an_account_with_no_usable_password(self, client):
        teacher = _make_teacher(email="unset@fiveaday.test")
        teacher.user.set_unusable_password()
        teacher.user.save()
        response = _logged_in(client, teacher).get(reverse("management"))
        assert response.context["can_change_password"] is False

    def test_hidden_when_the_session_has_no_django_user(self, authenticated_client):
        # `authenticated_client` sets the session flags without a Django user
        # at all, so there is no password to change.
        response = authenticated_client.get(reverse("management"))
        assert response.context["can_change_password"] is False
        assert b'id="btn-change-password"' not in response.content


class TestChangePassword:
    def _post(self, client, **payload):
        return client.post(
            reverse("change_password"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_success_changes_password_and_keeps_the_session(self, client):
        teacher = _make_teacher()
        _logged_in(client, teacher)

        response = self._post(
            client,
            current_password="old-password-1",
            new_password="new-password-9",
            confirm_password="new-password-9",
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

        teacher.user.refresh_from_db()
        assert teacher.user.check_password("new-password-9")
        # update_session_auth_hash — without it the password change logs the
        # user out of the very tab they are standing in.
        assert client.get(reverse("management")).status_code == 200

    def test_wrong_current_password_rejected(self, client):
        teacher = _make_teacher()
        _logged_in(client, teacher)

        response = self._post(
            client,
            current_password="not-the-password",
            new_password="new-password-9",
            confirm_password="new-password-9",
        )
        assert response.status_code == 400
        assert response.json()["success"] is False
        teacher.user.refresh_from_db()
        assert teacher.user.check_password("old-password-1")

    def test_mismatched_confirmation_rejected(self, client):
        teacher = _make_teacher()
        _logged_in(client, teacher)

        response = self._post(
            client,
            current_password="old-password-1",
            new_password="new-password-9",
            confirm_password="different-9",
        )
        assert response.status_code == 400
        teacher.user.refresh_from_db()
        assert teacher.user.check_password("old-password-1")

    # `settings_test.py` empties AUTH_PASSWORD_VALIDATORS for speed, so this
    # has to re-assert the real policy: settings.py raises the minimum length
    # to 12 because these accounts are effectively superusers over a database
    # of minors' personal data. Without the override the endpoint would accept
    # "123" here and nothing would notice the validators had been bypassed.
    @override_settings(
        AUTH_PASSWORD_VALIDATORS=[
            {
                "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
                "OPTIONS": {"min_length": 12},
            },
            {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
        ]
    )
    def test_weak_password_rejected(self, client):
        teacher = _make_teacher()
        _logged_in(client, teacher)

        response = self._post(
            client,
            current_password="old-password-1",
            new_password="short1",
            confirm_password="short1",
        )
        assert response.status_code == 400
        assert "12" in response.json()["message"]
        teacher.user.refresh_from_db()
        assert teacher.user.check_password("old-password-1")

    def test_invalid_json_returns_400(self, client):
        _logged_in(client, _make_teacher())
        response = client.post(
            reverse("change_password"),
            data="not-json",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_json_that_is_not_an_object_returns_400(self, client):
        # Valid JSON, wrong shape: parses fine and then 500s on .get().
        _logged_in(client, _make_teacher())
        response = client.post(
            reverse("change_password"),
            data=json.dumps(["new-password-9"]),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_get_not_allowed(self, client):
        _logged_in(client, _make_teacher())
        assert client.get(reverse("change_password")).status_code == 405

    def test_google_oauth_session_refused(self, client):
        teacher = _make_teacher(email="oauth2@fiveaday.test")
        _logged_in(client, teacher)
        session = client.session
        session["google_authenticated"] = True
        session.save()

        response = self._post(
            client,
            current_password="old-password-1",
            new_password="new-password-9",
            confirm_password="new-password-9",
        )
        assert response.status_code == 403
        teacher.user.refresh_from_db()
        assert teacher.user.check_password("old-password-1")

    def test_session_without_a_django_user_refused(self, authenticated_client):
        response = self._post(
            authenticated_client,
            current_password="whatever",
            new_password="new-password-9",
            confirm_password="new-password-9",
        )
        assert response.status_code == 403

    def test_non_admin_teacher_can_change_their_own(self, client):
        """Self-service, so `change_password` is in NON_ADMIN_ALLOWED_URL_NAMES —
        the endpoint only ever touches request.user."""
        teacher = _make_teacher(email="na-pw@fiveaday.test", admin=False)
        _logged_in(client, teacher)

        response = self._post(
            client,
            current_password="old-password-1",
            new_password="new-password-9",
            confirm_password="new-password-9",
        )
        assert response.status_code == 200
        teacher.user.refresh_from_db()
        assert teacher.user.check_password("new-password-9")

    def test_new_password_works_on_the_login_form(self, client):
        teacher = _make_teacher(email="relogin@fiveaday.test")
        _logged_in(client, teacher)
        self._post(
            client,
            current_password="old-password-1",
            new_password="new-password-9",
            confirm_password="new-password-9",
        )
        client.logout()

        with patch("core.views.auth._is_dev_env", return_value=False):
            response = client.post(
                reverse("login"),
                {"username": "relogin@fiveaday.test", "password": "new-password-9"},
            )
        assert response.status_code == 302
        assert response.url == reverse("home")
