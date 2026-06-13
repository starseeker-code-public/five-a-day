import logging as _logging
import os
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate as _django_authenticate
from django.contrib.auth import get_user_model
from django.contrib.auth import login as _django_login
from django.contrib.auth import logout as _django_logout
from django.shortcuts import redirect, render
from django.urls import reverse

if TYPE_CHECKING:
    from django.contrib.auth.models import User


def _is_dev_env() -> bool:
    """Dev basic-auth (env-var LOGIN_USERNAME/LOGIN_PASSWORD) only runs here."""
    return getattr(settings, "ENVIRONMENT", "development") == "development"


def _ensure_dev_user(username: str) -> "User":
    """
    Dev basic-auth path: get-or-create a Django superuser that matches the
    env-var username. OAuth and admin access all work through Django's auth
    system, so dev needs a concrete User too (even though password check is
    handled upstream by env-var comparison).
    """
    User = get_user_model()
    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            "email": os.getenv("DJANGO_SUPERUSER_EMAIL", f"{username}@local.dev"),
            "is_staff": True,
            "is_superuser": True,
        },
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    elif not (user.is_staff and user.is_superuser):
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])
    return user


def _finalize_session_login(request, user, display_name: str, *, google: bool = False):
    """Unified session setup used by every successful login path."""
    _django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["is_authenticated"] = True
    request.session["username"] = display_name
    if google:
        request.session["google_authenticated"] = True


def login_view(request):
    """
    Authentication dispatcher.

    - DJANGO_ENV=development: credentials compared against LOGIN_USERNAME /
      LOGIN_PASSWORD env vars (loaded from .env.development). A Django superuser
      mirroring the env username is get-or-created so admin access works.
    - Other environments: credentials compared against auth.User (Teachers log
      in with email + password). Django's ModelBackend does the hashing/check.
    """
    if request.session.get("is_authenticated"):
        return redirect("home")

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""

        if _is_dev_env():
            valid_username = os.getenv("LOGIN_USERNAME")
            valid_password = os.getenv("LOGIN_PASSWORD")

            if not valid_username or not valid_password:
                messages.error(
                    request,
                    "Login credentials not configured. Set LOGIN_USERNAME and LOGIN_PASSWORD in .env.development.",
                )
                return render(
                    request,
                    "login.html",
                    {
                        "google_oauth_available": False,
                        "password_reset_available": False,
                    },
                )

            if username == valid_username and password == valid_password:
                user = _ensure_dev_user(username)
                _finalize_session_login(request, user, username)
                return redirect("home")
            messages.error(request, "❌ Usuario o contraseña incorrectos")
        else:
            # Testing / production: authenticate Teachers via their linked User.
            user = _django_authenticate(request, username=username, password=password)
            if user is not None and user.is_active:
                display = getattr(user, "first_name", "") or user.get_username()
                _finalize_session_login(request, user, display)
                return redirect("home")
            messages.error(request, "❌ Usuario o contraseña incorrectos")

    google_oauth_available = bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))
    return render(
        request,
        "login.html",
        {
            "google_oauth_available": google_oauth_available,
            # Password reset only makes sense when Teacher-based auth is in use
            # (testing/production). In dev the password lives in .env.development.
            "password_reset_available": not _is_dev_env(),
        },
    )


def logout_view(request):
    """Log out of both Django auth and the custom session flag."""
    _django_logout(request)
    request.session.flush()
    messages.success(request, "✅ Has cerrado sesión correctamente")
    return redirect("login")


# ── Google OAuth helpers ─────────────────────────────────────────────

_GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/spreadsheets",
]

_oauth_log = _logging.getLogger(__name__)


def _google_callback_uri(request):
    """Return the OAuth callback URI — prefer explicit env var over build_absolute_uri."""
    explicit = os.getenv("GOOGLE_REDIRECT_URI")
    if explicit:
        return explicit
    return request.build_absolute_uri(reverse("google_oauth_callback"))


def _build_flow(client_id, client_secret, callback_uri, state=None):
    from google_auth_oauthlib.flow import Flow

    cfg = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [callback_uri],
        }
    }
    kwargs = {"scopes": _GOOGLE_SCOPES}
    if state:
        kwargs["state"] = state
    flow = Flow.from_client_config(cfg, **kwargs)
    flow.redirect_uri = callback_uri
    return flow


def _ensure_oauth_superuser(email: str, first_name: str) -> "User":
    """
    Get-or-create a Django superuser for the OAuth-authorised Google account.
    Links to an existing Teacher (by email) if one exists, otherwise creates a
    free-standing superuser record.
    """
    User = get_user_model()
    user, created = User.objects.get_or_create(
        username=email,
        defaults={
            "email": email,
            "first_name": first_name or "",
            "is_staff": True,
            "is_superuser": True,
        },
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])

    if not (user.is_staff and user.is_superuser):
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

    # If a Teacher exists with this email but isn't linked, link it now so the
    # whitelist logic in middleware sees the relationship.
    from students.models import Teacher

    teacher = Teacher.objects.filter(email__iexact=email).first()
    if teacher and teacher.user_id != user.pk:
        Teacher.objects.filter(pk=teacher.pk).update(user=user)

    return user


# ── Google OAuth views ───────────────────────────────────────────────


def google_oauth_redirect(request):
    """Redirect the browser to Google's OAuth2 consent screen."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        messages.error(request, "Google OAuth no está configurado.")
        return redirect("login")

    if settings.DEBUG:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    callback_uri = _google_callback_uri(request)
    _oauth_log.info("OAuth redirect → callback_uri=%s", callback_uri)
    flow = _build_flow(client_id, client_secret, callback_uri)
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="select_account",
    )
    request.session["google_oauth_state"] = state
    return redirect(authorization_url)


def google_oauth_callback(request):
    """
    Handle the OAuth2 redirect from Google and establish a session.

    On success: the authorised Google email is backed by a Django superuser
    (get-or-created) and logged in via django.contrib.auth.login, so the same
    session also grants access to /admin/ without a second login prompt.
    """
    import urllib.parse

    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    # Checked backend-only — never sent to the frontend
    allowed_email = (
        os.getenv("GOOGLE_ALLOWED_EMAIL") or os.getenv("EMAIL_HOST_USER") or os.getenv("DJANGO_SUPERUSER_EMAIL", "")
    )

    if settings.DEBUG:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    state = request.session.get("google_oauth_state")
    if not state or state != request.GET.get("state"):
        _oauth_log.warning("OAuth state mismatch: session=%s, param=%s", state, request.GET.get("state"))
        messages.error(request, "Estado OAuth inválido. Inténtalo de nuevo.")
        return redirect("login")

    callback_uri = _google_callback_uri(request)
    _oauth_log.info("OAuth callback → callback_uri=%s", callback_uri)
    flow = _build_flow(client_id, client_secret, callback_uri, state=state)

    # Reconstruct authorization_response using the configured base URI so it
    # matches exactly the redirect_uri registered in Google Console.
    parsed = urllib.parse.urlparse(callback_uri)
    query = request.META.get("QUERY_STRING", "")
    authorization_response = urllib.parse.urlunparse(parsed._replace(query=query))
    _oauth_log.info("OAuth callback → authorization_response=%s", authorization_response)

    try:
        flow.fetch_token(authorization_response=authorization_response)
    except Exception:
        _oauth_log.exception("OAuth fetch_token failed")
        messages.error(request, "Error al obtener el token de Google. Inténtalo de nuevo.")
        return redirect("login")

    credentials = flow.credentials

    try:
        id_info = id_token.verify_oauth2_token(
            credentials.id_token,
            google_requests.Request(),
            client_id,
        )
        user_email = id_info.get("email", "")
        user_name = id_info.get("given_name", user_email.split("@")[0])
    except Exception:
        _oauth_log.exception("OAuth id_token verification failed")
        messages.error(request, "Error al verificar la identidad de Google.")
        return redirect("login")

    # Backend-only check — email never exposed to frontend
    if user_email.lower() != allowed_email.lower():
        _oauth_log.warning("OAuth email mismatch: got=%s expected=%s", user_email, allowed_email)
        messages.error(request, "❌ Esta cuenta de Google no tiene acceso.")
        return redirect("login")

    user = _ensure_oauth_superuser(user_email, user_name)
    _finalize_session_login(request, user, user_name, google=True)

    # Store credentials so other views can reuse them for Gmail / Sheets
    request.session["google_credentials"] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes) if credentials.scopes else [],
    }
    return redirect("home")
