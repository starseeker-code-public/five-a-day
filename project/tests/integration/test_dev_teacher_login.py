"""
Development-environment Teacher login.

Until this change, `login_view` in development compared against
LOGIN_USERNAME / LOGIN_PASSWORD and nothing else, and that path always
get-or-creates a SUPERUSER. A non-admin Teacher therefore could not log in
locally at all, so the trimmed non-admin UI and `NON_ADMIN_ALLOWED_URL_NAMES`
were only testable on the QA VM. Development now falls back to Teacher auth
after the env-var check.

Also covers `TEACHER_SEED_<N>_USERNAME`: a short login handle must not REVOKE
the email login the teacher was already using.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from students.models import Teacher

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _dev_credentials(monkeypatch):
    monkeypatch.setenv("LOGIN_USERNAME", "admin")
    monkeypatch.setenv("LOGIN_PASSWORD", "admin-pw")


@pytest.fixture
def seeded_teacher():
    """A non-admin Teacher with a short login handle, as `seed_teachers`
    produces it from TEACHER_SEED_<N>_USERNAME."""
    teacher = Teacher.objects.create(
        first_name="Teacher",
        last_name="Demo",
        email="teacher@fiveaday.local",
        admin=False,
        active=True,
    )
    user = teacher.ensure_user(password="teacher-pw")
    user.username = "teacher"
    user.save(update_fields=["username"])
    return teacher


def _login(client, username, password):
    return client.post(reverse("login"), {"username": username, "password": password})


class TestDevelopmentLogin:
    @pytest.fixture(autouse=True)
    def _development(self, settings):
        settings.ENVIRONMENT = "development"

    def test_env_var_admin_login_still_works(self, client):
        response = _login(client, "admin", "admin-pw")
        assert response.status_code == 302
        assert client.session["is_authenticated"] is True
        assert User.objects.get(username="admin").is_superuser is True

    def test_env_var_login_mirrors_the_password_onto_the_user(self, client):
        """The dev superuser used to be created with set_unusable_password(),
        which is indistinguishable from a not-yet-activated teacher: the
        /management/ "Cambiar contraseña" button hid itself and
        /password-reset/ could not reach the account, so neither flow was
        exercisable from the dev login at all."""
        _login(client, "admin", "admin-pw")
        user = User.objects.get(username="admin")
        assert user.has_usable_password()
        assert user.check_password("admin-pw")

    def test_env_var_login_repairs_an_existing_unusable_password(self, client):
        # Every dev box provisioned before this has the superuser stored with
        # an unusable password, and that row is only ever re-read from here —
        # so `created` alone would never fix them.
        User.objects.create_superuser(username="admin", email="a@local.dev", password="whatever")
        user = User.objects.get(username="admin")
        user.set_unusable_password()
        user.save(update_fields=["password"])

        _login(client, "admin", "admin-pw")
        user.refresh_from_db()
        assert user.check_password("admin-pw")

    def test_env_var_login_does_not_clobber_a_changed_password(self, client):
        """A password the developer changed through the UI must survive the
        next login, or the button would appear to do nothing."""
        _login(client, "admin", "admin-pw")
        user = User.objects.get(username="admin")
        user.set_password("changed-in-the-ui")
        user.save(update_fields=["password"])

        # The env-var branch runs FIRST, so the .env value still logs in — the
        # developer cannot lock themselves out by changing it.
        response = _login(client, "admin", "admin-pw")
        assert response.status_code == 302
        user.refresh_from_db()
        assert user.check_password("changed-in-the-ui")

    def test_seeded_teacher_can_log_in_by_handle(self, client, seeded_teacher):
        response = _login(client, "teacher", "teacher-pw")
        assert response.status_code == 302
        assert client.session["is_authenticated"] is True

    def test_seeded_teacher_can_log_in_by_email(self, client, seeded_teacher):
        # The handle is an ADDITION, not a replacement.
        response = _login(client, "teacher@fiveaday.local", "teacher-pw")
        assert response.status_code == 302
        assert client.session["is_authenticated"] is True

    def test_teacher_login_does_not_grant_admin(self, client, seeded_teacher):
        _login(client, "teacher", "teacher-pw")
        assert seeded_teacher.user.is_staff is False
        assert seeded_teacher.user.is_superuser is False

    def test_wrong_password_is_refused(self, client, seeded_teacher):
        response = _login(client, "teacher", "nope")
        assert response.status_code == 200
        assert "is_authenticated" not in client.session

    def test_unknown_user_is_refused(self, client):
        response = _login(client, "nobody", "nope")
        assert response.status_code == 200
        assert "is_authenticated" not in client.session

    def test_missing_dev_credentials_still_allows_teacher_login(self, client, monkeypatch, seeded_teacher):
        monkeypatch.delenv("LOGIN_USERNAME", raising=False)
        monkeypatch.delenv("LOGIN_PASSWORD", raising=False)

        response = _login(client, "teacher", "teacher-pw")
        assert response.status_code == 302
        assert client.session["is_authenticated"] is True

    def test_missing_dev_credentials_reports_the_configuration_hint(self, client, monkeypatch):
        monkeypatch.delenv("LOGIN_USERNAME", raising=False)
        monkeypatch.delenv("LOGIN_PASSWORD", raising=False)

        response = _login(client, "admin", "admin-pw")
        assert response.status_code == 200
        assert b"LOGIN_USERNAME" in response.content


class TestEmailFallbackOutsideDevelopment:
    @pytest.fixture(autouse=True)
    def _testing(self, settings):
        settings.ENVIRONMENT = "testing"

    def test_handle_and_email_both_authenticate(self, client, seeded_teacher):
        assert _login(client, "teacher", "teacher-pw").status_code == 302

        client.logout()
        assert _login(client, "teacher@fiveaday.local", "teacher-pw").status_code == 302

    def test_env_var_credentials_are_ignored_outside_development(self, client):
        response = _login(client, "admin", "admin-pw")
        assert response.status_code == 200
        assert "is_authenticated" not in client.session

    def test_ambiguous_email_is_refused(self, client, seeded_teacher):
        # auth.User.email is NOT unique. Two users sharing an address must not
        # be resolved arbitrarily to whichever row came back first.
        User.objects.create_user(
            username="second-account",
            email="teacher@fiveaday.local",
            password="teacher-pw",
        )
        response = _login(client, "teacher@fiveaday.local", "teacher-pw")
        assert response.status_code == 200
        assert "is_authenticated" not in client.session

        # The unambiguous handle still works.
        assert _login(client, "teacher", "teacher-pw").status_code == 302
