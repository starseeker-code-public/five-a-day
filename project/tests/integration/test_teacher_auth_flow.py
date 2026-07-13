"""
Integration tests for the Teacher-based auth flow.

These tests simulate the testing/production environment (non-development)
where login goes through Django's ModelBackend using Teacher email+password.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from students.models import Teacher

User = get_user_model()
pytestmark = pytest.mark.django_db


def _make_teacher(email="t1@example.com", password="teach-pw", admin=False):
    teacher = Teacher.objects.create(
        first_name="Test",
        last_name="Teacher",
        email=email,
        admin=admin,
    )
    teacher.ensure_user(password=password)
    return teacher


@pytest.fixture
def non_dev_env():
    """Force login_view down the Teacher-auth branch."""
    with patch("core.views.auth._is_dev_env", return_value=False):
        yield


class TestTeacherLoginFlow:
    def test_admin_teacher_can_log_in(self, client, non_dev_env):
        _make_teacher(email="admin@fiveaday.test", password="my-pw", admin=True)
        response = client.post(
            reverse("login"),
            {"username": "admin@fiveaday.test", "password": "my-pw"},
        )
        assert response.status_code == 302
        assert response.url == reverse("home")
        assert client.session.get("is_authenticated") is True

    def test_non_admin_teacher_can_log_in(self, client, non_dev_env):
        _make_teacher(email="reg@fiveaday.test", password="reg-pw", admin=False)
        response = client.post(
            reverse("login"),
            {"username": "reg@fiveaday.test", "password": "reg-pw"},
        )
        assert response.status_code == 302
        assert response.url == reverse("home")

    def test_wrong_password_rejected(self, client, non_dev_env):
        _make_teacher(email="t@fiveaday.test", password="right-pw")
        response = client.post(
            reverse("login"),
            {"username": "t@fiveaday.test", "password": "WRONG"},
        )
        assert response.status_code == 200
        assert not client.session.get("is_authenticated")

    def test_inactive_user_rejected(self, client, non_dev_env):
        teacher = _make_teacher(email="x@fiveaday.test", password="pw")
        teacher.user.is_active = False
        teacher.user.save()
        response = client.post(
            reverse("login"),
            {"username": "x@fiveaday.test", "password": "pw"},
        )
        assert response.status_code == 200
        assert not client.session.get("is_authenticated")

    def test_password_reset_link_available_in_non_dev(self, client, non_dev_env):
        response = client.get(reverse("login"))
        assert response.context["password_reset_available"] is True

    def test_password_reset_link_hidden_in_dev(self, client):
        # Default (no patch): settings.ENVIRONMENT == "development"
        response = client.get(reverse("login"))
        assert response.context["password_reset_available"] is False


class TestNonAdminTeacherMiddleware:
    """Verify the non-admin whitelist blocks admin-only routes."""

    @pytest.fixture
    def non_admin_client(self, client):
        teacher = _make_teacher(email="na@fiveaday.test", password="pw", admin=False)
        # Simulate a fully-logged-in non-admin teacher
        client.force_login(teacher.user)
        session = client.session
        session["is_authenticated"] = True
        session["username"] = teacher.first_name
        session.save()
        return client

    @pytest.fixture
    def admin_client(self, client):
        teacher = _make_teacher(email="adm@fiveaday.test", password="pw", admin=True)
        client.force_login(teacher.user)
        session = client.session
        session["is_authenticated"] = True
        session["username"] = teacher.first_name
        session.save()
        return client

    def test_non_admin_blocked_from_payments(self, non_admin_client):
        response = non_admin_client.get("/payments/")
        # Middleware should redirect them to home with an error message
        assert response.status_code == 302
        assert response.url == reverse("home")

    def test_non_admin_can_access_schedule(self, non_admin_client):
        # Non-admin teachers may VIEW the schedule (save_schedule_slot stays admin-only).
        response = non_admin_client.get("/schedule/")
        assert response.status_code == 200

    def test_non_admin_blocked_from_all_info(self, non_admin_client):
        response = non_admin_client.get("/database/")
        assert response.status_code == 302

    def test_non_admin_blocked_from_apps(self, non_admin_client):
        response = non_admin_client.get("/apps/")
        assert response.status_code == 302

    def test_non_admin_api_endpoint_returns_403(self, non_admin_client):
        """API endpoints (/api/...) should return 403 JSON, not an HTML redirect."""
        response = non_admin_client.post(
            "/api/teachers/create/",
            data="{}",
            content_type="application/json",
        )
        assert response.status_code == 403
        assert response.json()["success"] is False

    def test_non_admin_can_access_dashboard(self, non_admin_client):
        response = non_admin_client.get("/")
        assert response.status_code == 200

    def test_non_admin_can_access_management_view_only(self, non_admin_client):
        response = non_admin_client.get("/management/")
        assert response.status_code == 200
        # is_admin_user flag should be False in context
        assert response.context["is_admin_user"] is False

    def test_non_admin_can_access_fun_friday(self, non_admin_client):
        response = non_admin_client.get("/fun-friday/")
        assert response.status_code == 200

    def test_non_admin_can_access_students_list(self, non_admin_client):
        response = non_admin_client.get("/students/")
        assert response.status_code == 200

    def test_admin_sees_payments(self, admin_client):
        response = admin_client.get("/payments/")
        # Admin bypasses the non-admin check — whatever status the view returns
        # it won't be redirected by the middleware to /.
        assert response.status_code != 302 or response.url != reverse("home")

    def test_admin_sees_is_admin_user_true(self, admin_client):
        response = admin_client.get("/")
        assert response.context["is_admin_user"] is True


class TestLogoutDjangoSession:
    def test_logout_clears_django_auth(self, client, non_dev_env):
        _make_teacher(email="lo@fiveaday.test", password="pw", admin=True)
        client.post(reverse("login"), {"username": "lo@fiveaday.test", "password": "pw"})
        assert client.session.get("_auth_user_id")
        client.get(reverse("logout"))
        assert not client.session.get("_auth_user_id")
        assert not client.session.get("is_authenticated")


class TestOAuthCreatesSuperuser:
    def test_oauth_callback_creates_django_user(self, client, monkeypatch):
        """Successful OAuth should create a Django superuser and log them in."""
        from unittest.mock import MagicMock

        monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "xyz")
        monkeypatch.setenv("GOOGLE_ALLOWED_EMAIL", "allowed@example.com")

        session = client.session
        session["google_oauth_state"] = "abc"
        session.save()

        mock_flow = MagicMock()
        mock_flow.fetch_token.return_value = None
        creds = MagicMock()
        creds.id_token = "fake_token"
        creds.token = "access"
        creds.refresh_token = "r"
        creds.token_uri = "https://oauth2.googleapis.com/token"
        creds.client_id = "abc"
        creds.client_secret = "xyz"
        creds.scopes = ["openid"]
        mock_flow.credentials = creds

        with (
            patch("core.views.auth._build_flow", return_value=mock_flow),
            patch(
                "google.oauth2.id_token.verify_oauth2_token",
                return_value={"email": "allowed@example.com", "given_name": "Al"},
            ),
        ):
            response = client.get(reverse("google_oauth_callback") + "?state=abc&code=x")

        assert response.status_code == 302
        assert response.url == reverse("home")
        user = User.objects.get(username="allowed@example.com")
        assert user.is_staff is True
        assert user.is_superuser is True
        # Logged in via Django auth
        assert client.session.get("_auth_user_id") == str(user.pk)
        assert client.session.get("google_authenticated") is True

    def test_oauth_promotes_existing_non_staff_user(self, client, monkeypatch):
        """If a User exists with the OAuth email but isn't superuser, OAuth promotes them."""
        from unittest.mock import MagicMock

        User.objects.create_user(
            username="promoted@example.com",
            email="promoted@example.com",
            password="whatever",
            is_staff=False,
            is_superuser=False,
        )
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "xyz")
        monkeypatch.setenv("GOOGLE_ALLOWED_EMAIL", "promoted@example.com")

        session = client.session
        session["google_oauth_state"] = "abc"
        session.save()

        mock_flow = MagicMock()
        mock_flow.fetch_token.return_value = None
        creds = MagicMock()
        creds.id_token = "fake_token"
        for attr in ("token", "refresh_token", "token_uri", "client_id", "client_secret"):
            setattr(creds, attr, "x")
        creds.scopes = []
        mock_flow.credentials = creds

        with (
            patch("core.views.auth._build_flow", return_value=mock_flow),
            patch(
                "google.oauth2.id_token.verify_oauth2_token",
                return_value={"email": "promoted@example.com", "given_name": "P"},
            ),
        ):
            response = client.get(reverse("google_oauth_callback") + "?state=abc&code=x")

        assert response.status_code == 302
        promoted = User.objects.get(username="promoted@example.com")
        assert promoted.is_staff is True
        assert promoted.is_superuser is True

    def test_oauth_links_existing_teacher(self, client, monkeypatch):
        """If a Teacher exists with the OAuth email, the User gets linked to it."""
        from unittest.mock import MagicMock

        monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "xyz")
        monkeypatch.setenv("GOOGLE_ALLOWED_EMAIL", "linked@example.com")

        teacher = Teacher.objects.create(
            first_name="Linked",
            last_name="Teacher",
            email="linked@example.com",
            admin=True,
        )
        assert teacher.user_id is None

        session = client.session
        session["google_oauth_state"] = "abc"
        session.save()

        mock_flow = MagicMock()
        mock_flow.fetch_token.return_value = None
        creds = MagicMock()
        creds.id_token = "fake_token"
        for attr in ("token", "refresh_token", "token_uri", "client_id", "client_secret"):
            setattr(creds, attr, "x")
        creds.scopes = []
        mock_flow.credentials = creds

        with (
            patch("core.views.auth._build_flow", return_value=mock_flow),
            patch(
                "google.oauth2.id_token.verify_oauth2_token",
                return_value={"email": "linked@example.com", "given_name": "Linked"},
            ),
        ):
            client.get(reverse("google_oauth_callback") + "?state=abc&code=x")

        teacher.refresh_from_db()
        assert teacher.user_id is not None
        assert teacher.user.username == "linked@example.com"
