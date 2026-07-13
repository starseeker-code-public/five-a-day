"""Integration tests for the 2FA views and login gate (v1.13)."""

from unittest.mock import patch

import pyotp
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.services import two_factor_service as tfs

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def admin_teacher_with_user(teacher):
    teacher.admin = True
    teacher.save()
    user = teacher.ensure_user(password="p4ssword-with-something")
    return teacher, user


@pytest.fixture
def admin_client(client, admin_teacher_with_user):
    """Client with a fully-authenticated admin session (no 2FA gate)."""
    _, user = admin_teacher_with_user
    client.force_login(user)
    session = client.session
    session["is_authenticated"] = True
    session["username"] = user.first_name or user.username
    session.save()
    return client


class TestSetupView:
    def test_non_admin_bounces_to_home(self, client, teacher):
        # teacher is admin=False by default; log them in
        teacher.admin = False
        teacher.save()
        user = teacher.ensure_user(password="pw")
        client.force_login(user)
        session = client.session
        session["is_authenticated"] = True
        session.save()
        response = client.get(reverse("two_factor_setup"))
        assert response.status_code == 302
        assert response.url == reverse("home")

    def test_get_renders_qr_and_backup_codes(self, admin_client, admin_teacher_with_user):
        response = admin_client.get(reverse("two_factor_setup"))
        assert response.status_code == 200
        assert response.context["qr_png_base64"]
        # Fresh enrolment → backup codes shown once
        assert len(response.context["backup_codes"]) == 8

    def test_post_with_valid_code_enables_2fa(self, admin_client, admin_teacher_with_user):
        teacher, _ = admin_teacher_with_user
        # Trigger the GET first to stage the secret
        admin_client.get(reverse("two_factor_setup"))
        teacher.refresh_from_db()
        code = pyotp.TOTP(teacher.two_factor_secret).now()
        response = admin_client.post(reverse("two_factor_setup"), {"code": code})
        assert response.status_code == 302
        assert response.url == reverse("two_factor_manage")
        teacher.refresh_from_db()
        assert teacher.two_factor_enabled is True

    def test_post_with_bad_code_re_renders_page(self, admin_client, admin_teacher_with_user):
        teacher, _ = admin_teacher_with_user
        admin_client.get(reverse("two_factor_setup"))
        response = admin_client.post(reverse("two_factor_setup"), {"code": "000000"})
        assert response.status_code == 200
        teacher.refresh_from_db()
        assert teacher.two_factor_enabled is False


class TestManageView:
    def test_redirects_to_setup_when_not_enrolled(self, admin_client):
        response = admin_client.get(reverse("two_factor_manage"))
        assert response.status_code == 302
        assert response.url == reverse("two_factor_setup")

    def test_renders_when_enrolled(self, admin_client, admin_teacher_with_user):
        teacher, _ = admin_teacher_with_user
        tfs.begin_enrolment(teacher)
        teacher.two_factor_enabled = True
        teacher.save()
        response = admin_client.get(reverse("two_factor_manage"))
        assert response.status_code == 200

    def test_rotate_action_regenerates_codes(self, admin_client, admin_teacher_with_user):
        teacher, _ = admin_teacher_with_user
        tfs.begin_enrolment(teacher)
        teacher.two_factor_enabled = True
        teacher.save()
        old_hashes = list(teacher.two_factor_backup_codes)
        response = admin_client.post(reverse("two_factor_manage"), {"action": "rotate"})
        assert response.status_code == 200
        assert len(response.context["new_backup_codes"]) == 8
        teacher.refresh_from_db()
        assert teacher.two_factor_backup_codes != old_hashes

    def test_disable_action_wipes(self, admin_client, admin_teacher_with_user):
        teacher, _ = admin_teacher_with_user
        tfs.begin_enrolment(teacher)
        teacher.two_factor_enabled = True
        teacher.save()
        response = admin_client.post(reverse("two_factor_manage"), {"action": "disable"})
        assert response.status_code == 302
        assert response.url == reverse("home")
        teacher.refresh_from_db()
        assert teacher.two_factor_enabled is False
        assert teacher.two_factor_secret == ""


class TestLoginGateFlow:
    def test_login_without_2fa_stays_direct(self, client, admin_teacher_with_user):
        """User without 2FA enrolled → login lands on home directly."""
        teacher, user = admin_teacher_with_user
        with patch("core.views.auth._is_dev_env", return_value=False):
            response = client.post(
                reverse("login"),
                {"username": user.username, "password": "p4ssword-with-something"},
            )
        assert response.status_code == 302
        assert response.url == reverse("home")

    def test_login_with_2fa_via_real_login_view(self, client, admin_teacher_with_user):
        """The real path through login_view: password OK + 2FA on → verify."""
        teacher, user = admin_teacher_with_user
        tfs.begin_enrolment(teacher)
        teacher.two_factor_enabled = True
        teacher.save()
        with patch("core.views.auth._is_dev_env", return_value=False):
            response = client.post(
                reverse("login"),
                {"username": user.username, "password": "p4ssword-with-something"},
            )
        assert response.status_code == 302
        assert response.url == reverse("two_factor_verify")
        assert client.session.get("is_authenticated") is None

    def test_login_with_2fa_redirects_to_verify(self, client, admin_teacher_with_user):
        from core.views.auth import _needs_two_factor

        teacher, user = admin_teacher_with_user
        tfs.begin_enrolment(teacher)
        teacher.two_factor_enabled = True
        teacher.save()

        # Simulate the mid-login gate by patching the branch directly. The
        # env-dependent path in login_view is exercised via _needs_two_factor
        # + _stage_pending_2fa which we cover here.
        assert _needs_two_factor(user) is True

        request_client = client
        session = request_client.session
        _stage_pending_2fa_via_session(session, user)

        # Now hitting /two-factor/verify/ picks up the pending user
        response = request_client.get(reverse("two_factor_verify"))
        assert response.status_code == 200
        assert response.context["email"] == user.email

    def test_verify_valid_code_finishes_login(self, client, admin_teacher_with_user):
        teacher, user = admin_teacher_with_user
        payload = tfs.begin_enrolment(teacher)
        teacher.refresh_from_db()
        teacher.two_factor_enabled = True
        teacher.save()

        session = client.session
        _stage_pending_2fa_via_session(session, user)

        code = pyotp.TOTP(payload.secret).now()
        response = client.post(reverse("two_factor_verify"), {"code": code})
        assert response.status_code == 302
        assert response.url == reverse("home")
        # Session flipped to authenticated
        assert client.session.get("is_authenticated") is True

    def test_verify_bad_code_stays_on_page(self, client, admin_teacher_with_user):
        teacher, user = admin_teacher_with_user
        tfs.begin_enrolment(teacher)
        teacher.two_factor_enabled = True
        teacher.save()

        session = client.session
        _stage_pending_2fa_via_session(session, user)

        response = client.post(reverse("two_factor_verify"), {"code": "000000"})
        assert response.status_code == 200
        assert client.session.get("is_authenticated") is None

    def test_verify_backup_code_also_works(self, client, admin_teacher_with_user):
        teacher, user = admin_teacher_with_user
        payload = tfs.begin_enrolment(teacher)
        teacher.two_factor_enabled = True
        teacher.save()

        session = client.session
        _stage_pending_2fa_via_session(session, user)

        response = client.post(reverse("two_factor_verify"), {"code": payload.backup_codes[0]})
        assert response.status_code == 302
        assert response.url == reverse("home")

    def test_verify_without_pending_user_redirects(self, client):
        response = client.get(reverse("two_factor_verify"))
        assert response.status_code == 302
        assert response.url == reverse("login")


def _stage_pending_2fa_via_session(session, user):
    from core.views.two_factor import _PENDING_USER_SESSION_KEY

    session[_PENDING_USER_SESSION_KEY] = user.id
    session.save()


class TestResetTwoFactorCommand:
    def test_reset_disables_2fa(self, teacher):
        tfs.begin_enrolment(teacher)
        teacher.two_factor_enabled = True
        teacher.save()

        from django.core.management import call_command

        call_command("reset_two_factor", teacher.email)
        teacher.refresh_from_db()
        assert teacher.two_factor_enabled is False
        assert teacher.two_factor_secret == ""

    def test_unknown_email_errors(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("reset_two_factor", "nobody@nowhere.com")
