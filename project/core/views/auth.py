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

from core.log_safe import safe_log
from core.rate_limit import rate_limit

if TYPE_CHECKING:
    from django.contrib.auth.models import User


def _is_dev_env() -> bool:
    """Dev basic-auth (env-var LOGIN_USERNAME/LOGIN_PASSWORD) only runs here."""
    return getattr(settings, "ENVIRONMENT", "development") == "development"


def _ensure_dev_user(username: str, password: str | None = None) -> "User":
    """
    Dev basic-auth path: get-or-create a Django superuser that matches the
    env-var username. OAuth and admin access all work through Django's auth
    system, so dev needs a concrete User too (even though the credential check
    is handled upstream by env-var comparison).

    The env-var password is MIRRORED onto that user, because a user with no
    usable password is indistinguishable from a not-yet-activated teacher: the
    /management/ "Cambiar contraseña" button hides itself for such accounts
    (`can_change_own_password`) and `/password-reset/` cannot reach them, so the
    dev login could not exercise either flow at all. This is safe in dev and
    only in dev — `LOGIN_PASSWORD` is already the plaintext login credential in
    `.env`, so storing its hash adds no exposure, and `_is_dev_env()` gates the
    only caller.

    Set only when the stored password is UNUSABLE, matching `seed_teachers`:
    a password the developer later changed through the UI must survive the next
    login, or the button would appear to do nothing. Note the env-var branch in
    `login_view` runs first, so the `.env` value keeps working regardless —
    changing the password here cannot lock anyone out of their own dev box.
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
    update_fields = []
    if created or not user.has_usable_password():
        # `created` is not enough on its own: every dev box provisioned before
        # this had the superuser written with set_unusable_password(), and those
        # rows are only ever re-read from here.
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        update_fields.append("password")
    if not (user.is_staff and user.is_superuser):
        user.is_staff = True
        user.is_superuser = True
        update_fields += ["is_staff", "is_superuser"]
    if update_fields:
        user.save(update_fields=update_fields)
    return user


def _authenticate_teacher(request, identifier: str, password: str):
    """
    Authenticate against auth.User, accepting EITHER the login handle or the
    email address as the identifier.

    `seed_teachers` can give a Teacher a short `TEACHER_SEED_<N>_USERNAME`
    ("claudia") while `Teacher.email` stays the real address. Django's
    ModelBackend only ever matches `User.username`, so without this fallback
    setting a handle would silently REVOKE the email login that teacher had
    been using. Both are accepted; the handle is tried first.
    """
    user = _django_authenticate(request, username=identifier, password=password)
    if user is not None:
        return user

    if "@" not in identifier:
        return None

    User = get_user_model()
    # `.only()` — we need the username to re-authenticate, nothing else. Emails
    # are not unique on auth.User, so an ambiguous match is refused rather than
    # resolved arbitrarily.
    candidates = list(User.objects.filter(email__iexact=identifier).only("username")[:2])
    if len(candidates) != 1:
        return None
    return _django_authenticate(request, username=candidates[0].username, password=password)


def _finalize_session_login(request, user, display_name: str, *, google: bool = False):
    """Unified session setup used by every successful login path."""
    _django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["is_authenticated"] = True
    request.session["username"] = display_name
    if google:
        request.session["google_authenticated"] = True
    # Reset the expiry to the configured age. A 2FA login arrives here via
    # `_stage_pending_2fa`, which set a 5-minute expiry to bound the OTP step;
    # cycle_key preserves it, so without this a 2FA-enabled admin got a session
    # that idle-expired in 5 minutes instead of the normal 6 hours.
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)


def _needs_two_factor(user) -> bool:
    """True iff `user` has a linked Teacher with 2FA enabled."""
    teacher = getattr(user, "teacher", None)
    return teacher is not None and teacher.two_factor_enabled


def _stage_pending_2fa(request, user):
    """
    Password check succeeded but the user has 2FA enabled — stash the user
    id on the session (WITHOUT setting `is_authenticated`) so the /two-
    factor/verify/ page can finish the login. `SimpleAuthMiddleware` treats
    the session as unauthenticated until the OTP is confirmed.
    """
    from core.views.two_factor import _PENDING_USER_SESSION_KEY

    # Clear any leftover pre-auth state from a previous attempt
    request.session.flush()
    request.session[_PENDING_USER_SESSION_KEY] = user.id
    request.session.set_expiry(300)  # 5 minutes to complete the second factor


@rate_limit("login", limit=5, window_seconds=60)
def login_view(request):
    """
    Authentication dispatcher.

    - DJANGO_ENV=development: credentials compared against LOGIN_USERNAME /
      LOGIN_PASSWORD env vars (loaded from .env.development). A Django superuser
      mirroring the env username is get-or-created so admin access works.
    - Other environments: credentials compared against auth.User (Teachers log
      in with email + password). Django's ModelBackend does the hashing/check.

    v1.10: rate-limited to 5 POSTs / minute / IP.
    """
    if request.session.get("is_authenticated"):
        return redirect("home")

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""

        dev_env = _is_dev_env()
        dev_creds_configured = False

        if dev_env:
            valid_username = os.getenv("LOGIN_USERNAME")
            valid_password = os.getenv("LOGIN_PASSWORD")
            dev_creds_configured = bool(valid_username and valid_password)

            if dev_creds_configured and username == valid_username and password == valid_password:
                user = _ensure_dev_user(username, valid_password)
                # Dev-mode users never have a Teacher record → no 2FA gate.
                _finalize_session_login(request, user, username)
                return redirect("home")

        # Teacher auth via the linked auth.User. This is the ONLY path in
        # testing/production, and in development it is the fallback after the
        # env-var admin login above — which is what makes a seeded non-admin
        # Teacher (TEACHER_SEED_1_* in .env.development) usable locally. Without
        # it, dev could only ever log in as a superuser, so the trimmed
        # non-admin UI and NON_ADMIN_ALLOWED_URL_NAMES were untestable outside
        # the QA VM.
        user = _authenticate_teacher(request, username, password)
        if user is not None and user.is_active:
            if _needs_two_factor(user):
                # Password OK but 2FA enrolled → force the second factor.
                _stage_pending_2fa(request, user)
                return redirect("two_factor_verify")

            display = getattr(user, "first_name", "") or user.get_username()
            _finalize_session_login(request, user, display)
            return redirect("home")

        if dev_env and not dev_creds_configured:
            messages.error(
                request,
                "Login credentials not configured. Set LOGIN_USERNAME and LOGIN_PASSWORD in .env.development.",
            )
        else:
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

# Identity only. `gmail.send` and `spreadsheets` were requested here until
# v1.23.0 and nothing ever used them: email goes out over SMTP with an app
# password (EMAIL_BACKEND is the SMTP backend), and the Sheets export
# authenticates as a SERVICE ACCOUNT via core.services.google_sheets_service.
# Asking for them meant every sign-in minted a token that could send mail as the
# academy and read/write its spreadsheets — see the credential-storage note in
# google_oauth_callback. Do not add scopes here for a feature that does not read
# `credentials` in this module.
_GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
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
    #
    # `Teacher.email` is unique, but Postgres uniqueness is case-SENSITIVE while
    # this lookup is iexact — so `Ana@x.com` and `ana@x.com` can coexist and
    # `.first()` would pick the lower pk, which decides the linked account's
    # admin status. Refuse an ambiguous match (link nothing) rather than guess;
    # an admin resolves the duplicate. `linked_user` on the Teacher signal keeps
    # the flags in sync once linked.
    from students.models import Teacher

    matches = list(Teacher.objects.filter(email__iexact=email)[:2])
    if len(matches) == 1:
        teacher = matches[0]
        if teacher.user_id != user.pk:
            Teacher.objects.filter(pk=teacher.pk).update(user=user)
    elif len(matches) > 1:
        _oauth_log.error(
            "OAuth link: refusing an ambiguous Teacher email match (%d rows differ only by case)", len(matches)
        )

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
    # No `access_type="offline"`: this flow authenticates a human at the keyboard
    # and never acts on their behalf later, so there is nothing for a refresh
    # token to be used for — and a refresh token is the one OAuth artefact worth
    # stealing, because it does not expire.
    authorization_url, state = flow.authorization_url(
        include_granted_scopes="true",
        prompt="select_account",
    )
    request.session["google_oauth_state"] = state
    # google-auth-oauthlib >=1.3 enables PKCE by default: authorization_url()
    # generates a one-time code_verifier and stores it on THIS Flow instance,
    # sending only the code_challenge to Google. The callback builds a fresh
    # Flow (a new request, a new object) that has no verifier, so the token
    # exchange fails with "invalid_grant: Missing code verifier". Carry it
    # across the same way `state` is carried — same session, same browser.
    request.session["google_oauth_code_verifier"] = flow.code_verifier
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
        # Log only whether each side was present -- the state value is a
        # CSRF token (sensitive) and attacker-controlled on the query-string
        # side, so it must not reach the log verbatim.
        _oauth_log.warning(
            "OAuth state mismatch: session_state_present=%s, param_state_present=%s",
            bool(state),
            bool(request.GET.get("state")),
        )
        messages.error(request, "Estado OAuth inválido. Inténtalo de nuevo.")
        return redirect("login")

    callback_uri = _google_callback_uri(request)
    _oauth_log.info("OAuth callback → callback_uri=%s", callback_uri)
    flow = _build_flow(client_id, client_secret, callback_uri, state=state)
    # Restore the PKCE verifier generated at redirect time (see
    # google_oauth_redirect). Without it, fetch_token sends no code_verifier and
    # Google rejects the grant. pop() so a replayed callback cannot reuse it.
    flow.code_verifier = request.session.pop("google_oauth_code_verifier", None)

    # Reconstruct authorization_response using the configured base URI so it
    # matches exactly the redirect_uri registered in Google Console.
    parsed = urllib.parse.urlparse(callback_uri)
    query = request.META.get("QUERY_STRING", "")
    authorization_response = urllib.parse.urlunparse(parsed._replace(query=query))
    # Built from the raw QUERY_STRING, so it is attacker-controlled -- strip
    # line breaks before it reaches the log.
    _oauth_log.info("OAuth callback → authorization_response=%s", safe_log(authorization_response))

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
        email_verified = bool(id_info.get("email_verified"))
        user_name = id_info.get("given_name", user_email.split("@")[0])
    except Exception:
        _oauth_log.exception("OAuth id_token verification failed")
        messages.error(request, "Error al verificar la identidad de Google.")
        return redirect("login")

    # Backend-only check — email never exposed to frontend.
    #
    # Fail CLOSED on empty values. `allowed_email` falls back through three env
    # vars to "", and `id_info.get("email", "")` is "" when the token carries no
    # email claim — so a bare `!=` comparison let "" == "" through and handed a
    # SUPERUSER account to a token with no identity in it. Both sides must be
    # non-empty, and Google must say the address is verified: an unverified
    # claim is an address the holder has not proven they control.
    if not allowed_email:
        _oauth_log.error("OAuth allow-list is empty — set GOOGLE_ALLOWED_EMAIL. Refusing sign-in.")
        messages.error(request, "❌ El acceso con Google no está configurado.")
        return redirect("login")

    if not user_email or not email_verified:
        _oauth_log.warning("OAuth rejected: email_present=%s verified=%s", bool(user_email), email_verified)
        messages.error(request, "❌ Esta cuenta de Google no tiene acceso.")
        return redirect("login")

    if user_email.lower() != allowed_email.lower():
        _oauth_log.warning("OAuth email mismatch: got=%s expected=%s", safe_log(user_email), allowed_email)
        messages.error(request, "❌ Esta cuenta de Google no tiene acceso.")
        return redirect("login")

    user = _ensure_oauth_superuser(user_email, user_name)

    # OAuth still has to clear the 2FA gate when the linked Teacher enrolled
    # the second factor — the OAuth-confirmed email is only one factor.
    if _needs_two_factor(user):
        _stage_pending_2fa(request, user)
        return redirect("two_factor_verify")

    _finalize_session_login(request, user, user_name, google=True)

    # Nothing is kept from `credentials`. Until v1.23.0 this stored the access
    # token, the REFRESH token and the OAuth CLIENT SECRET in the session — and
    # SESSION_ENGINE is the database backend, so each of those landed in a
    # `django_session` row as base64 (signed, not encrypted), captured by every
    # Cloud SQL backup and by scripts/export_prod_db.sh. No view ever read them:
    # the only consumer was the 2FA hand-off, which just wrote them back. If a
    # feature ever genuinely needs Google API access, give it a service account
    # like google_sheets_service has — do not re-add this.
    return redirect("home")
