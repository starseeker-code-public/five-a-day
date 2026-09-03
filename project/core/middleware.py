"""
Middleware — authentication, QA error reporting, and teacher view whitelisting.
"""

import logging
import secrets
import traceback

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import SESSION_KEY as DJANGO_AUTH_SESSION_KEY
from django.core.mail import send_mail
from django.shortcuts import redirect
from django.urls import resolve, reverse
from django.views.debug import SafeExceptionReporterFilter

logger = logging.getLogger(__name__)


class QAErrorEmailMiddleware:
    """
    When QAConfiguration.error_email_enabled is True, catches unhandled
    exceptions and sends a detailed report to SUPPORT_EMAIL.
    Must be placed AFTER SecurityMiddleware and BEFORE other app middleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response

    # Form fields whose values must never reach an inbox. Matched
    # case-insensitively against the `key` of each `key=value` pair in the body
    # preview below.
    #
    # AUDITED AGAINST EVERY WRITE PATH IN THE APP (v1.27.1) rather than grown
    # anecdotally, because the miss is silent: the key is simply absent, the
    # value is mailed in cleartext, and nothing fails. The inventory, endpoint
    # by endpoint:
    #   /login/                        username, password
    #   /password-reset/               email
    #   /password-reset/confirm/…      new_password1, new_password2
    #   /api/password-change/  (JSON)  current_password, new_password,
    #                                  confirm_password   ← was MISSING, and it
    #                                  carries the SAME plaintext as
    #                                  `new_password`, so a 500 in the
    #                                  change-password flow on the QA VM mailed
    #                                  the new staff password to SUPPORT_EMAIL.
    #                                  That is the second time this list has
    #                                  been short by exactly one confirm field.
    #   /two-factor/{verify,setup,manage}/   code   (+ `action`, not secret)
    #   /parent/login/                 email, password
    #   /parent/change-password/       current_password, password,
    #                                  password_confirm
    #   /parent/forgot-password/       email
    #   /admin/…/password/             old_password, password1, password2
    # The remaining entries are defensive aliases for the same concepts (a new
    # form naming its field `otp` or `backup_code` must not have to remember to
    # come back here) plus two PII fields that are not credentials but are
    # regulated data about minors' families.
    REDACT_KEYS = frozenset(
        {
            # Passwords, in every spelling used or plausibly used.
            "password",
            "password1",
            "password2",
            "new_password1",
            "new_password2",
            "old_password",
            "current_password",
            "new_password",
            "password_confirm",
            "confirm_password",
            "new_password_confirm",
            "temporary_password",
            # Second factor. `code` is the field name all three 2FA views post;
            # the rest are aliases so a rename cannot silently un-redact it.
            "code",
            "otp",
            "otp_code",
            "backup_code",
            "totp",
            "totp_code",
            "two_factor_code",
            "two_factor_secret",
            "verification_code",
            # Tokens and shared secrets.
            "token",
            "csrfmiddlewaretoken",
            "secret",
            "secret_key",
            "api_key",
            "access_token",
            "refresh_token",
            "id_token",
            "client_secret",
            "signature",
            "probe_token",
            # Not credentials — regulated personal data.
            "dni",
            "iban",
        }
    )

    @classmethod
    def _redact_body(cls, raw: str) -> str:
        """Blank the values of credential-bearing fields in a request body.

        The preview is genuinely useful for debugging a QA failure, but an
        exception raised on the login POST would otherwise email a working
        password in cleartext, and the same applies to the password-reset
        confirm form and the 2FA code. Redact rather than drop the preview.

        Handles BOTH encodings a form can arrive in. Only doing urlencoded
        would cover the login form but silently miss every multipart POST —
        which is what the backlog-screenshot endpoint sends, and what Django's
        own RequestFactory produces by default.
        """
        if "Content-Disposition:" in raw and "name=" in raw:
            return cls._redact_multipart(raw)
        # JSON bodies (every /api/ POST, including the password-change
        # endpoints) used to fall straight through the `"=" not in raw` bail-out
        # and reach the inbox verbatim.
        if raw.lstrip().startswith(("{", "[")):
            return cls._redact_json(raw)
        if "=" not in raw:
            return raw
        out = []
        for pair in raw.split("&"):
            key, sep, _value = pair.partition("=")
            if sep and key.strip().lower() in cls.REDACT_KEYS:
                out.append(f"{key}=[REDACTED]")
            else:
                out.append(pair)
        return "&".join(out)

    @classmethod
    def _redact_json(cls, raw: str) -> str:
        """Blank sensitive values in a (possibly truncated) JSON body preview.

        Regex-based for the same reason `_redact_multipart` is: the 500-char
        preview is usually cut mid-document, so `json.loads` would fail and
        leave the secret in place.
        """
        import re

        def _replace(match: "re.Match[str]") -> str:
            if match.group("key").strip().lower() not in cls.REDACT_KEYS:
                return match.group(0)
            return f'{match.group("head")}"[REDACTED]"'

        pattern = re.compile(r'(?P<head>"(?P<key>[^"]+)"\s*:\s*)"(?:[^"\\]|\\.)*(?:"|$)')
        return pattern.sub(_replace, raw)

    @classmethod
    def _redact_multipart(cls, raw: str) -> str:
        """Blank the payload of any multipart part whose name is sensitive.

        Deliberately regex-based on the already-truncated 500-char preview
        rather than a real MIME parse: the preview is usually a fragment with no
        closing boundary, so a parser would simply fail and leave the value in
        place. Matches `name="x"` and everything up to the next boundary.
        """
        import re

        def _replace(match: "re.Match[str]") -> str:
            name = match.group("name")
            if name.strip().lower() not in cls.REDACT_KEYS:
                return match.group(0)
            return f"{match.group('head')}[REDACTED]{match.group('tail')}"

        pattern = re.compile(
            r'(?P<head>name="(?P<name>[^"]+)"[^\r\n]*\r?\n(?:[^\r\n]+\r?\n)*\r?\n)'
            r"(?P<value>.*?)"
            r"(?P<tail>(?=\r?\n--)|\Z)",
            re.DOTALL,
        )
        return pattern.sub(_replace, raw)

    def process_exception(self, request, exception):
        try:
            from core.models import QAConfiguration

            # QA instrumentation, so it must never run outside the QA
            # environment. The flag lives in the database, and the middleware
            # used to honour it wherever it was set — including production,
            # where the dashboard that toggles it is unreachable but the row is
            # still writable from a shell.
            if not getattr(settings, "IS_TESTING_ENV", False):
                return None

            config = QAConfiguration.get_config()
            if not config.error_email_enabled:
                return None

            support_email = getattr(settings, "SUPPORT_EMAIL", None)
            if not support_email:
                return None

            tb = traceback.format_exception(type(exception), exception, exception.__traceback__)
            tb_str = "".join(tb)

            username = request.session.get("username", "anonymous") if hasattr(request, "session") else "—"
            method = request.method
            path = request.get_full_path()
            body_preview = ""
            try:
                body_preview = self._redact_body(request.body[:500].decode("utf-8", errors="replace"))
            except Exception:
                # Best-effort only. `request.body` raises if the stream was
                # already consumed (file uploads, streaming parsers); the error
                # report is still worth sending without the body preview, and
                # this handler must never raise while handling an exception.
                pass

            subject = f"[ERROR] {type(exception).__name__} at {path}"
            body = (
                f"AUTOMATED ERROR REPORT — Five a Day QA\n"
                f"{'=' * 60}\n\n"
                f"Exception:   {type(exception).__name__}: {exception}\n"
                f"Path:        {method} {path}\n"
                f"User:        {username}\n"
                f"Version:     {settings.APP_VERSION}\n"
                f"Environment: {settings.ENVIRONMENT}\n"
                f"Debug:       {settings.DEBUG}\n"
                f"Server time: {__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
                f"REQUEST BODY (first 500 chars):\n"
                f"{body_preview or '(empty)'}\n\n"
                f"TRACEBACK:\n"
                f"{'-' * 60}\n"
                f"{tb_str}\n"
                f"{'=' * 60}\n"
            )

            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[support_email],
                fail_silently=True,
            )
        except Exception:
            logger.exception("QAErrorEmailMiddleware failed to send error email")

        return None  # Let Django's default error handling continue


class RedactingExceptionReporterFilter(SafeExceptionReporterFilter):
    """Extend Django's POST scrubbing to the field names THIS app posts.

    Wired up as `DEFAULT_EXCEPTION_REPORTER_FILTER`, so it governs the debug
    500 page AND the body of every `AdminEmailHandler` mail (see
    `settings.ADMINS` — production alerting).

    Django's `hidden_settings` regex (``API|TOKEN|KEY|SECRET|PASS|SIGNATURE``)
    covers settings and local variables — NOT the request body. `POST` is only
    cleansed when the view opted in with `@sensitive_post_parameters`, which
    Django's own auth views do and NONE of this app's custom ones do:
    `login_view`, `change_password`, `two_factor_verify`,
    `parent_portal_login` and `parent_portal_change_password` are all
    hand-rolled. So without this subclass an error report from any of them
    carries the submitted password verbatim.

    Both mailers read the SAME list, so there is one place to add a new field
    name — `QAErrorEmailMiddleware.REDACT_KEYS`.
    """

    def get_post_parameters(self, request):
        cleansed = super().get_post_parameters(request)
        if request is None:
            return cleansed
        try:
            hits = [k for k in cleansed if str(k).strip().lower() in QAErrorEmailMiddleware.REDACT_KEYS]
        except TypeError:  # pragma: no cover — defensive, `cleansed` is a mapping
            return cleansed
        if not hits:
            return cleansed
        # `super()` hands back `request.POST` ITSELF (an IMMUTABLE QueryDict)
        # whenever the view does not use @sensitive_post_parameters, so copy
        # before writing: assigning into it would raise, and mutating the live
        # request object from an error handler would be worse.
        cleansed = cleansed.copy()
        for key in hits:
            cleansed[key] = self.cleansed_substitute
        return cleansed


# URL name whitelist for non-admin teachers. Any URL name not in this set is
# blocked. Admin-only write endpoints inside the management page are called out
# explicitly (the list view itself — `management` — is allowed so non-admins
# can see it in view-only mode). Keep this list in sync with core/urls.py and
# the per-app urls.py files.
NON_ADMIN_ALLOWED_URL_NAMES = frozenset(
    {
        # Auth + public
        "login",
        "logout",
        "google_oauth_redirect",
        "google_oauth_callback",
        "password_reset",
        "password_reset_done",
        "password_reset_confirm",
        "password_reset_complete",
        "health_check",
        # Dashboard
        "home",
        # Students — READ-ONLY. A non-admin teacher may browse the roll and open
        # a ficha, nothing else: `student_create`, `student_update`,
        # `enroll_student` and `parent_create` are deliberately absent, so
        # creating, enrolling and editing a student are admin-only.
        "students_list",
        "student_detail",
        "search_students",
        # Waiting list (v1.1) — non-admins manage the QUEUE (browse it, take a
        # new entry over the phone), but neither door out of it: promotion
        # (`assign_from_waiting_list`) is enrolling by another name, and
        # `add_to_waiting_list` cancels the student's ACTIVE enrollment — a
        # one-way financial write that stops billing the family, with the only
        # undo path (re-enrolment) being admin-only. Both stay admin-only,
        # consistent with students being read-only for this role since v1.26.8.
        "waiting_list",
        "waiting_list_create",
        # Schedule — view-only for non-admin teachers (save_schedule_slot stays admin-only)
        "schedule_view",
        # Fun Friday (attendance view + attendance API)
        "fun_friday_view",
        "add_fun_friday_attendance",
        "remove_fun_friday_attendance",
        "toggle_fun_friday_this_week",
        # Management page — view-only. Write endpoints below are NOT in this list:
        #   update_site_config, create_teacher, create_group,
        #   update_enrollment_modality  (admin-only writes)
        "management",
        "api_get_teachers",
        # Changing your OWN password is self-service, not an admin write: the
        # view only ever touches request.user and refuses OAuth sessions.
        "change_password",
        "language_cheque_students",
        # Expenses are GONE from this list entirely (v1.27.1). The previous
        # compromise — read + create, no edit or delete — never had a rationale
        # that survived being written down: `expenses_list` IS the academy's
        # profit-and-loss for the month (every rent, salary and software line,
        # plus the totals), which is precisely the class of figure the trimmed
        # non-admin dashboard exists to withhold, and `create_expense` is a
        # financial WRITE against it. The sidebar has always hidden the Gastos
        # link from this role (`base.html` gates it on is_admin_user), so the
        # page was reachable only by typing the URL — i.e. nobody was using it,
        # and the two entries were pure latent grant. Reports
        # (`reports_view`/`reports_pdf`) went for the same reason in v1.26.8.
        # Two-factor auth (v1.13) — verify is reached mid-login (pre-session)
        # so it's already in PUBLIC_PREFIXES. Setup/manage are admin-only and
        # deliberately absent from this whitelist.
        "two_factor_verify",
        # Todos + support. `history_list` is admin-only — the actions-history
        # feed and the notifications bell are both hidden from non-admin
        # teachers in `base.html`, and the endpoint has to agree.
        "create_todo",
        "complete_todo",
        "submit_support_ticket",
        # Error test pages (harmless)
        "test_error_400",
        "test_error_403",
        "test_error_404",
        "test_error_405",
        "test_error_500",
    }
)


def _session_has_django_identity(request) -> bool:
    """True when this session was opened by a real `django.contrib.auth.login`.

    EVERY login path calls it — the dev env-var login (`_ensure_dev_user`),
    teacher email+password, the 2FA hand-off and the OAuth callback all go
    through `_finalize_session_login` — so a genuine session always carries
    `SESSION_KEY`. Sessions are stored server-side (`django_session`), so a
    client cannot fabricate one either.

    A session flagged `is_authenticated` WITHOUT this key therefore does not
    occur in any environment; it is the shape the test suite's
    `authenticated_client` fixture builds. The checks below treat it as the
    legacy shape rather than rejecting it, so the distinction is: "no Django
    identity was ever claimed" (leave alone) versus "one was claimed and can no
    longer be resolved" (reject).
    """
    session = getattr(request, "session", None)
    if session is None:
        return False
    return bool(session.get(DJANGO_AUTH_SESSION_KEY))


def _session_identity_revoked(request) -> bool:
    """True when a session flagged authenticated has lost its backing identity.

    Layer 1 used to check only the `is_authenticated` SESSION FLAG, and nothing
    ever revokes that flag. `SESSION_COOKIE_AGE` is 6 h with
    `SESSION_SAVE_EVERY_REQUEST=True`, so an open tab renews it indefinitely —
    an offboarded teacher kept working access for as long as they kept the tab
    alive.

    Worse than "kept their own access": all three offboarding actions
    (deactivating the auth.User, deactivating the Teacher — which
    `_sync_linked_user_flags` mirrors onto `User.is_active` — and DELETING the
    Teacher, which `_deactivate_orphaned_user` mirrors the same way) make
    `ModelBackend.get_user` refuse the id, so `request.user` becomes
    AnonymousUser. `_is_non_admin_teacher` then answered "not a non-admin", and
    the session was handed the UNRESTRICTED set: /payments/, /database/,
    /management/ write endpoints, the lot. Revocation ESCALATED the session.

    The `is_superuser` / `is_staff` escape hatches are preserved deliberately:
    the development env-var login and every OAuth session are free-standing
    superusers with no Teacher row, and both must keep working.
    """
    if not _session_has_django_identity(request):
        return False

    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        # The id in the session no longer resolves to a usable account:
        # deleted, deactivated, or the password was rotated (which
        # invalidates the session auth hash). All three mean "revoked".
        return True

    # Reverse accessor from students.Teacher.user's related_name="teacher".
    # Checked BEFORE the is_superuser/is_staff hatch for the same reason as in
    # `_is_non_admin_teacher`: those flags are mirrored from `Teacher.admin`, so
    # a deactivated admin still carries them and would read as "not revoked".
    teacher = getattr(user, "teacher", None)
    if teacher is not None and not teacher.active:
        return True

    if user.is_superuser or user.is_staff:
        return False

    if teacher is None:
        # An ordinary auth.User with no Teacher row is not staff of this
        # academy — either an account whose Teacher was deleted, or a bare row
        # added at /admin/auth/user/add/. It used to be merely *restricted*;
        # rejecting it is the safe direction, and there is no legitimate
        # account of that shape (a real one is provisioned by
        # `Teacher.ensure_user`, `seed_teachers` or the OAuth callback).
        return True
    return not teacher.active


def _is_non_admin_teacher(request) -> bool:
    """True iff the request's session must be restricted to the URL whitelist.

    DEFAULT-DENY: an authenticated auth.User with NO linked Teacher row is
    restricted, not trusted. `_authenticate_teacher` logs in any auth.User, so
    `teacher is None → full access` meant a bare user added at
    /admin/auth/user/add/ (a bookkeeper, an integration account) — or a teacher
    whose OneToOne an admin cleared intending to REVOKE access — walked straight
    past the whitelist into /database/, /payments/ and the config endpoints.
    Admin access is an explicit grant: a superuser/staff flag or Teacher.admin.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        # FAIL CLOSED. A session that CLAIMS a Django login it can no longer
        # resolve (deactivated, deleted, password rotated) is restricted, never
        # trusted — answering False here is what turned offboarding into
        # privilege escalation. `SimpleAuthMiddleware` now rejects such a
        # session outright, but this predicate is also read by
        # `core.context_processors.today_notifications` with no such gate in
        # front of it, so the fail-closed answer has to live here too.
        #
        # A session with no Django identity at all is the legacy/test shape (see
        # `_session_has_django_identity`) and keeps its previous answer.
        return _session_has_django_identity(request)
    # Reverse accessor defined in students.Teacher.user's related_name="teacher".
    teacher = getattr(user, "teacher", None)
    if teacher is not None and not teacher.active:
        # `active` is the offboarding switch, and it is tested BEFORE the
        # superuser/staff hatch below on purpose. The Teacher `post_save` signal
        # mirrors `Teacher.admin` onto is_staff AND is_superuser, so a
        # deactivated ADMIN teacher still carries both flags — checking the
        # flags first therefore handed the full unrestricted set to somebody who
        # had just been let go, which is precisely the escalation this predicate
        # exists to prevent. Reading `admin` off a deactivated row has the same
        # problem, hence the early return.
        return True
    if user.is_superuser or user.is_staff:
        return False
    if teacher is None:
        return True
    return not teacher.admin


class SimpleAuthMiddleware:
    """
    Enforces access control in three parts:

    0. The parent portal (`/parent/…`) is a SEPARATE authentication domain with
       its own session key, checked first and never subject to the staff login
       or the teacher whitelist. Default-deny: only the URL names in
       `PORTAL_PUBLIC_URL_NAMES` are reachable without a portal session.
    1. Authentication: every non-public URL requires an authenticated session
       (either via env-var basic-auth in dev, Teacher login in testing/prod, or
       Google OAuth). Unauthenticated requests are redirected to /login/. The
       session FLAG is not sufficient — the identity behind it must still exist
       and still be active (`_session_identity_revoked`).
    2. Authorization: requests made by a non-admin teacher are restricted to
       the URL-name whitelist above. Blocked routes → 403 (or a redirect to the
       dashboard with a flash message for non-API routes).
    """

    PUBLIC_PREFIXES = (
        "/health/",
        "/static/",
        "/media/",
        "/auth/google/",
        "/password-reset/",
        "/api/stripe/webhook/",  # v1.11: called by Stripe's servers, signed via header
        "/manifest.webmanifest",  # v1.12: PWA manifest, must be public
        "/sw.js",  # v1.12: service worker, must be public
        "/two-factor/verify/",  # v1.13: mid-login 2FA gate (pre-session)
    )

    #: The parent portal keeps its OWN session (`parent_id`) and never sets
    #: `is_authenticated`, so it cannot be checked by layer 1 below — it used to
    #: sit in PUBLIC_PREFIXES as a blanket `/parent/`, which exempted every
    #: current AND FUTURE portal URL from authentication. That is backwards: a
    #: new portal view was public until somebody remembered to call
    #: `_require_parent` inside it. The prefix is now gated on the portal
    #: session, with the genuinely public URLs ENUMERATED, so a new portal URL
    #: is protected by default. The views' own `_require_parent` remains the
    #: real check (it also verifies the credential stamp and the
    #: must-change-password pin); this is the default-deny net under it.
    PORTAL_URL_PREFIX = "/parent/"
    PORTAL_PUBLIC_URL_NAMES = frozenset(
        {
            "parent_portal_login",
            "parent_portal_forgot_password",
        }
    )
    #: Portal endpoints answering JSON. A missing session gets a 401 body rather
    #: than a 302 to an HTML login page, which is what `fetch()` needs (and what
    #: `create_checkout_link` has always returned).
    PORTAL_JSON_URL_NAMES = frozenset({"stripe_create_checkout_link"})

    def __init__(self, get_response):
        self.get_response = get_response

    def _portal_gate(self, request, path):
        """Default-deny for `/parent/…`, or None to let the request through."""
        try:
            url_name = resolve(path).url_name
        except Exception:
            url_name = None

        if url_name in self.PORTAL_PUBLIC_URL_NAMES:
            return None
        if request.session.get("parent_id"):
            return None
        if url_name in self.PORTAL_JSON_URL_NAMES:
            from django.http import JsonResponse

            return JsonResponse({"success": False, "error": "not authenticated"}, status=401)
        return redirect("parent_portal_login")

    def __call__(self, request):
        login_url = reverse("login")
        path = request.path

        # The portal is a separate authentication domain — checked first, and
        # never subject to the staff login redirect or the teacher whitelist.
        if path.startswith(self.PORTAL_URL_PREFIX):
            blocked = self._portal_gate(request, path)
            return blocked if blocked is not None else self.get_response(request)

        is_public = path == login_url or any(path.startswith(prefix) for prefix in self.PUBLIC_PREFIXES)

        # Layer 1: authentication
        if not is_public and not request.session.get("is_authenticated"):
            return redirect("login")

        # Layer 1b: the flag is not enough — the identity behind it must still
        # exist and still be active. See `_session_identity_revoked`.
        if not is_public and _session_identity_revoked(request):
            logger.warning("Rejecting a session whose backing identity is no longer valid; forcing re-login")
            request.session.flush()
            messages.error(request, "❌ Tu sesión ya no es válida. Vuelve a iniciar sesión.")
            return redirect("login")

        # Layer 2: non-admin teacher whitelist
        if not is_public and _is_non_admin_teacher(request):
            try:
                url_name = resolve(path).url_name
            except Exception:
                url_name = None

            if url_name not in NON_ADMIN_ALLOWED_URL_NAMES:
                # AJAX / API endpoints: return a plain 403 JSON response so the
                # frontend sees a real error instead of an HTML redirect body.
                if path.startswith("/api/"):
                    from django.http import JsonResponse

                    return JsonResponse(
                        {"success": False, "error": "No tienes permiso para esta acción."},
                        status=403,
                    )
                messages.error(request, "❌ No tienes permiso para acceder a esa sección.")
                return redirect("home")

        return self.get_response(request)


class NoHtmlCacheMiddleware:
    """Prevent browsers from caching dynamic HTML pages.

    Static assets are content-hashed and served `immutable`, but the HTML that
    references them had no `Cache-Control`, so browsers heuristically cached the
    page — pinning it to OLD hashed CSS/JS and showing a stale theme after a
    deploy (fixed only by a hard refresh). Marking HTML `no-cache` forces a
    revalidation on every navigation, so the current asset hashes always load.
    Static/media responses (served by WhiteNoise) are untouched.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.get("Content-Type", "")
        if content_type.startswith("text/html") and not response.has_header("Cache-Control"):
            response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response


class SecurityHeadersMiddleware:
    """Adds Content-Security-Policy and Permissions-Policy to HTML responses.

    Django's SecurityMiddleware covers HSTS, nosniff, referrer-policy and
    X-Frame-Options, but has never emitted a CSP. This app had three stored-XSS
    holes in v1.15 (history messages reachable by a non-admin teacher, the
    payment autocomplete, and inlined JSON), so a policy that neutralises the
    *consequences* of the next one is worth having even though the escaping
    discipline currently holds.

    Report-only by default. Since v1.24.0 `script-src` is **nonce-based**: the
    middleware mints a per-request nonce, exposes it as `request.csp_nonce`
    (templates read it via the `csp_nonce` context processor), and every inline
    `<script>` block carries `nonce="{{ csp_nonce }}"`. There are no inline
    event handlers left anywhere in the templates, so a browser that honours
    the nonce executes ONLY our own script blocks and the allowlisted CDN —
    injected markup cannot run. `'unsafe-inline'` stays in the list purely as a
    legacy fallback: CSP2+ browsers ignore it whenever a nonce is present.
    `style-src` keeps `'unsafe-inline'` for real — the templates use inline
    `style=""` attributes throughout and Tailwind's CDN injects `<style>`.
    Run report-only first, watch the console, then set `CSP_ENFORCE=True` once
    the reports are clean.

    `frame-ancestors`, `base-uri` and `object-src` are the parts that carry real
    weight here and cost nothing: they cannot be bypassed by inline script and
    they close clickjacking, `<base>` hijacking and legacy plugin vectors.
    """

    #: Kept as a tuple of (directive, value) so the order is stable in tests.
    #: `{nonce}` in script-src is filled per request in __call__.
    DIRECTIVES = (
        ("default-src", "'self'"),
        # The nonce authorises exactly our own inline blocks. 'unsafe-inline'
        # is dead weight to any browser that understands nonces (they ignore
        # it when one is present) and only serves pre-2016 browsers.
        ("script-src", "'self' 'nonce-{nonce}' 'unsafe-inline'"),
        ("style-src", "'self' 'unsafe-inline' https://fonts.googleapis.com"),
        ("font-src", "'self' https://fonts.gstatic.com data:"),
        ("img-src", "'self' data: blob:"),
        ("connect-src", "'self'"),
        # No plugins, no <base> rewriting, no framing. Not weakened by
        # 'unsafe-inline' above, which is what makes these the useful part.
        ("object-src", "'none'"),
        ("base-uri", "'none'"),
        ("frame-ancestors", "'none'"),
        ("form-action", "'self'"),
    )

    PERMISSIONS_POLICY = "geolocation=(), microphone=(), camera=(), payment=(), usb=(), interest-cohort=()"

    def __init__(self, get_response):
        self.get_response = get_response
        self.policy_template = "; ".join(f"{name} {value}" for name, value in self.DIRECTIVES)

    def __call__(self, request):
        # Minted BEFORE the view runs so templates can stamp it onto their
        # inline <script> blocks. Safe to vary per response because
        # NoHtmlCacheMiddleware already marks every HTML response no-store —
        # a cached page with yesterday's nonce can never be served.
        request.csp_nonce = secrets.token_urlsafe(16)
        response = self.get_response(request)

        # HTML only. Adding a CSP to a PDF or a CSV download achieves nothing
        # and shows up as noise in report collectors.
        if not response.get("Content-Type", "").startswith("text/html"):
            return response

        enforce = getattr(settings, "CSP_ENFORCE", False)
        header = "Content-Security-Policy" if enforce else "Content-Security-Policy-Report-Only"
        if not response.has_header(header):
            response[header] = self.policy_template.format(nonce=request.csp_nonce)
        if not response.has_header("Permissions-Policy"):
            response["Permissions-Policy"] = self.PERMISSIONS_POLICY
        return response
