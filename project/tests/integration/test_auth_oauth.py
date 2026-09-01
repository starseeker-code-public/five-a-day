"""Tests for the OAuth branches of core.views.auth.

These cover the lines that test_auth_views.py doesn't: login_view error
paths, logout message, google_oauth_redirect, and google_oauth_callback.
The Google OAuth flow is mocked since we can't make real requests.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


# ============================================================================
# login_view — non-trivial branches
# ============================================================================


class TestLoginView:
    def test_already_authenticated_redirects_home(self, authenticated_client):
        response = authenticated_client.get(reverse("login"))
        assert response.status_code == 302
        assert response.url == reverse("home")

    def test_missing_credentials_env_shows_error(self, client, monkeypatch):
        monkeypatch.delenv("LOGIN_USERNAME", raising=False)
        monkeypatch.delenv("LOGIN_PASSWORD", raising=False)
        response = client.post(reverse("login"), {"username": "x", "password": "y"})
        assert response.status_code == 200
        # Error message rendered, not a redirect

    def test_oauth_available_flag_set_when_env_present(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "xyz")
        response = client.get(reverse("login"))
        assert response.status_code == 200
        assert response.context["google_oauth_available"] is True


class TestLogoutView:
    def test_clears_session_and_redirects(self, authenticated_client):
        response = authenticated_client.get(reverse("logout"))
        assert response.status_code == 302
        assert response.url == reverse("login")


# ============================================================================
# google_oauth_redirect
# ============================================================================


class TestGoogleOauthRedirect:
    def test_missing_credentials_redirects_to_login(self, client, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        response = client.get(reverse("google_oauth_redirect"))
        assert response.status_code == 302
        assert response.url == reverse("login")

    def test_builds_flow_and_redirects_to_google(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "xyz")

        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = ("https://accounts.google.com/oauth", "state123")

        with patch("core.views.auth._build_flow", return_value=mock_flow):
            response = client.get(reverse("google_oauth_redirect"))

        assert response.status_code == 302
        assert response.url.startswith("https://accounts.google.com/")

    def test_explicit_redirect_uri_is_preferred(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "xyz")
        monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://explicit.example.com/cb")

        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = ("https://accounts.google.com/oauth", "s")
        with patch("core.views.auth._build_flow", return_value=mock_flow) as mocked:
            client.get(reverse("google_oauth_redirect"))
        # _build_flow was called with callback_uri = explicit env value
        args, kwargs = mocked.call_args
        assert "explicit.example.com" in args[2] or "explicit.example.com" in str(kwargs)


# ============================================================================
# google_oauth_callback
# ============================================================================


class TestGoogleOauthCallback:
    def _setup_session_state(self, client, state="abc"):
        session = client.session
        session["google_oauth_state"] = state
        session.save()
        return client

    def test_missing_state_redirects_to_login(self, client):
        response = client.get(reverse("google_oauth_callback") + "?state=foo")
        assert response.status_code == 302
        assert response.url == reverse("login")

    def test_state_mismatch_redirects_to_login(self, client):
        self._setup_session_state(client, state="abc")
        response = client.get(reverse("google_oauth_callback") + "?state=different")
        assert response.status_code == 302
        assert response.url == reverse("login")

    def test_fetch_token_failure_redirects_to_login(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "xyz")
        self._setup_session_state(client, state="abc")

        mock_flow = MagicMock()
        mock_flow.fetch_token.side_effect = Exception("token failure")
        with patch("core.views.auth._build_flow", return_value=mock_flow):
            response = client.get(reverse("google_oauth_callback") + "?state=abc&code=x")
        assert response.status_code == 302
        assert response.url == reverse("login")

    def test_id_token_verification_failure_redirects(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "xyz")
        monkeypatch.setenv("GOOGLE_ALLOWED_EMAIL", "allowed@example.com")
        self._setup_session_state(client, state="abc")

        mock_flow = MagicMock()
        mock_flow.fetch_token.return_value = None
        creds = MagicMock()
        creds.id_token = "fake_token"
        mock_flow.credentials = creds

        with (
            patch("core.views.auth._build_flow", return_value=mock_flow),
            patch("google.oauth2.id_token.verify_oauth2_token", side_effect=Exception("bad id")),
        ):
            response = client.get(reverse("google_oauth_callback") + "?state=abc&code=x")
        assert response.status_code == 302
        assert response.url == reverse("login")

    def test_wrong_email_redirects_to_login(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "xyz")
        monkeypatch.setenv("GOOGLE_ALLOWED_EMAIL", "allowed@example.com")
        self._setup_session_state(client, state="abc")

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
                return_value={"email": "intruder@evil.com", "email_verified": True, "given_name": "I"},
            ),
        ):
            response = client.get(reverse("google_oauth_callback") + "?state=abc&code=x")
        assert response.status_code == 302
        assert response.url == reverse("login")

    def _mock_flow(self):
        mock_flow = MagicMock()
        mock_flow.fetch_token.return_value = None
        creds = MagicMock()
        creds.id_token = "fake_token"
        mock_flow.credentials = creds
        return mock_flow

    def test_unverified_email_is_rejected(self, client, monkeypatch):
        """An unverified claim is an address the holder has not proven they own."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "xyz")
        monkeypatch.setenv("GOOGLE_ALLOWED_EMAIL", "allowed@example.com")
        self._setup_session_state(client, state="abc")

        with (
            patch("core.views.auth._build_flow", return_value=self._mock_flow()),
            patch(
                "google.oauth2.id_token.verify_oauth2_token",
                return_value={"email": "allowed@example.com", "email_verified": False, "given_name": "T"},
            ),
        ):
            response = client.get(reverse("google_oauth_callback") + "?state=abc&code=x")

        assert response.status_code == 302
        assert response.url == reverse("login")
        assert client.session.get("is_authenticated") is not True

    def test_empty_allow_list_fails_closed(self, client, monkeypatch):
        """`"" == ""` used to grant a SUPERUSER to a token carrying no email."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "xyz")
        monkeypatch.delenv("GOOGLE_ALLOWED_EMAIL", raising=False)
        monkeypatch.delenv("EMAIL_HOST_USER", raising=False)
        monkeypatch.delenv("DJANGO_SUPERUSER_EMAIL", raising=False)
        self._setup_session_state(client, state="abc")

        with (
            patch("core.views.auth._build_flow", return_value=self._mock_flow()),
            patch(
                "google.oauth2.id_token.verify_oauth2_token",
                return_value={"email": "", "email_verified": True, "given_name": ""},
            ),
        ):
            response = client.get(reverse("google_oauth_callback") + "?state=abc&code=x")

        assert response.status_code == 302
        assert response.url == reverse("login")
        assert client.session.get("is_authenticated") is not True

    def test_success_establishes_session_and_redirects_home(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "xyz")
        monkeypatch.setenv("GOOGLE_ALLOWED_EMAIL", "allowed@example.com")
        self._setup_session_state(client, state="abc")

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
                return_value={"email": "allowed@example.com", "email_verified": True, "given_name": "Test"},
            ),
        ):
            response = client.get(reverse("google_oauth_callback") + "?state=abc&code=x")

        assert response.status_code == 302
        assert response.url == reverse("home")
        # Session should be marked authenticated
        assert client.session.get("is_authenticated") is True
        assert client.session.get("google_authenticated") is True
