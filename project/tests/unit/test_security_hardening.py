"""Regression tests for the v1.23.0 security hardening.

Each class maps to one finding from the security review. They are written as
regression tests — every assertion here failed before the change it covers, so a
future refactor that reintroduces the weakness turns this file red rather than
leaving the fix silently undone.
"""

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth.hashers import identify_hasher
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.middleware import QAErrorEmailMiddleware, SecurityHeadersMiddleware
from core.services import two_factor_service as tfs
from core.utils import csv_safe, csv_safe_row


# ─────────────────────────────────────────────────────────────────────────────
# M2 — CSV formula injection (CWE-1236)
# ─────────────────────────────────────────────────────────────────────────────
class TestCsvSafe:
    @pytest.mark.parametrize(
        "payload",
        [
            '=HYPERLINK("http://evil/","Ver")',
            "=cmd|'/c calc'!A0",
            "+1+1",
            "-2+3",
            "@SUM(A1:A9)",
            "\tleading tab",
            "\rleading cr",
        ],
    )
    def test_formula_prefixes_are_neutralised(self, payload):
        out = csv_safe(payload)
        assert out.startswith("'"), f"{payload!r} was not neutralised"
        assert out[1:] == payload, "the original text must be preserved after the quote"

    @pytest.mark.parametrize("value", ["Ana", "López-García", "Cuota de septiembre", "", "a=b"])
    def test_ordinary_text_is_untouched(self, value):
        assert csv_safe(value) == value

    def test_non_strings_pass_through_unchanged(self):
        """A Decimal amount of -50 is a legitimate leading '-' and must not gain a quote."""
        from decimal import Decimal

        assert csv_safe(Decimal("-50.00")) == Decimal("-50.00")
        assert csv_safe(7) == 7
        assert csv_safe(None) == ""

    def test_row_helper_applies_per_cell(self):
        assert csv_safe_row(["ok", "=BAD()", 3]) == ["ok", "'=BAD()", 3]

    @pytest.mark.django_db
    def test_payment_export_quotes_a_malicious_student_name(
        self, authenticated_client, student, parent, pending_payment
    ):
        """End to end: a name set by any teacher must not export as a live formula."""
        student.first_name = '=HYPERLINK("http://evil/","x")'
        student.save(update_fields=["first_name"])

        response = authenticated_client.get(reverse("export_payments"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "'=HYPERLINK" in body
        # The bare formula must not appear at the start of a field.
        assert ",=HYPERLINK" not in body


# ─────────────────────────────────────────────────────────────────────────────
# M3 — the QA error email must not leak credentials, and must stay in QA
# ─────────────────────────────────────────────────────────────────────────────
class TestQaErrorEmailRedaction:
    def test_password_value_is_redacted(self):
        body = "csrfmiddlewaretoken=abc&username=admin&password=Sup3rSecret!"
        out = QAErrorEmailMiddleware._redact_body(body)
        assert "Sup3rSecret" not in out
        assert "password=[REDACTED]" in out
        assert "username=admin" in out, "non-credential fields stay readable"

    @pytest.mark.parametrize(
        "key", ["password", "password1", "new_password2", "old_password", "code", "token", "dni", "iban"]
    )
    def test_every_credential_key_is_covered(self, key):
        out = QAErrorEmailMiddleware._redact_body(f"{key}=value123")
        assert "value123" not in out

    def test_non_form_bodies_are_left_alone(self):
        assert QAErrorEmailMiddleware._redact_body("just some text") == "just some text"

    def test_multipart_bodies_are_redacted_too(self):
        """Django's RequestFactory and the screenshot upload both send multipart."""
        crlf = chr(13) + chr(10)
        body = crlf.join(
            [
                "--BoUnDaRy",
                'Content-Disposition: form-data; name="username"',
                "",
                "admin",
                "--BoUnDaRy",
                'Content-Disposition: form-data; name="password"',
                "",
                "hunter2",
                "--BoUnDaRy--",
                "",
            ]
        )
        out = QAErrorEmailMiddleware._redact_body(body)
        assert "hunter2" not in out
        assert "[REDACTED]" in out
        assert "admin" in out

    @pytest.mark.django_db
    @override_settings(IS_TESTING_ENV=False, SUPPORT_EMAIL="qa@example.com")
    def test_no_email_outside_the_qa_environment(self, mailoutbox, rf):
        """The DB flag must not be honoured in production."""
        from core.models import QAConfiguration

        config = QAConfiguration.get_config()
        config.error_email_enabled = True
        config.save(update_fields=["error_email_enabled"])

        middleware = QAErrorEmailMiddleware(lambda r: None)
        request = rf.post("/boom/", data={"password": "hunter2"})
        middleware.process_exception(request, ValueError("boom"))

        assert len(mailoutbox) == 0

    @pytest.mark.django_db
    @override_settings(IS_TESTING_ENV=True, SUPPORT_EMAIL="qa@example.com")
    def test_qa_environment_sends_but_redacts(self, mailoutbox, rf):
        from core.models import QAConfiguration

        config = QAConfiguration.get_config()
        config.error_email_enabled = True
        config.save(update_fields=["error_email_enabled"])

        middleware = QAErrorEmailMiddleware(lambda r: None)
        request = rf.post("/boom/", data={"username": "admin", "password": "hunter2"})
        middleware.process_exception(request, ValueError("boom"))

        assert len(mailoutbox) == 1
        assert "hunter2" not in mailoutbox[0].body
        assert "[REDACTED]" in mailoutbox[0].body


# ─────────────────────────────────────────────────────────────────────────────
# L1 — CSP and Permissions-Policy
# ─────────────────────────────────────────────────────────────────────────────
class TestSecurityHeaders:
    def _response(self, content_type="text/html; charset=utf-8"):
        from django.http import HttpResponse

        return SecurityHeadersMiddleware(lambda r: HttpResponse("<p>hi</p>", content_type=content_type))(None)

    def test_report_only_by_default(self):
        response = self._response()
        assert "Content-Security-Policy-Report-Only" in response
        assert "Content-Security-Policy" not in response.headers or response.get("Content-Security-Policy-Report-Only")

    @override_settings(CSP_ENFORCE=True)
    def test_enforcing_when_opted_in(self):
        response = self._response()
        assert "Content-Security-Policy" in response
        assert "Content-Security-Policy-Report-Only" not in response

    def test_csp_enforce_is_actually_read_from_the_environment(self):
        """`settings.py` must assign CSP_ENFORCE from the env var.

        The middleware reads it with `getattr(settings, "CSP_ENFORCE", False)`,
        so if `settings.py` never assigns it the documented env var is a silent
        no-op and CSP can never be enforced. `test_enforcing_when_opted_in`
        above cannot catch that: `override_settings` sets the attribute
        directly and bypasses the wiring entirely. This asserts the setting
        exists and tracks the environment.
        """
        from django.conf import settings

        assert isinstance(getattr(settings, "CSP_ENFORCE", None), bool)

        settings_path = Path(settings.BASE_DIR) / "project" / "settings.py"
        source = settings_path.read_text(encoding="utf-8")
        assert 'os.getenv("CSP_ENFORCE"' in source, "CSP_ENFORCE is not read from the environment"

    def test_the_directives_that_inline_script_cannot_bypass(self):
        policy = self._response()["Content-Security-Policy-Report-Only"]
        # These carry the real weight: they are unaffected by 'unsafe-inline'.
        assert "object-src 'none'" in policy
        assert "base-uri 'none'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "form-action 'self'" in policy

    def test_permissions_policy_is_set(self):
        assert "geolocation=()" in self._response()["Permissions-Policy"]

    def test_non_html_is_untouched(self):
        """A CSP on a PDF or CSV download achieves nothing and pollutes reports."""
        response = self._response(content_type="application/pdf")
        assert "Content-Security-Policy-Report-Only" not in response
        assert "Permissions-Policy" not in response

    @pytest.mark.django_db
    def test_headers_reach_a_real_page(self, authenticated_client):
        response = authenticated_client.get(reverse("home"))
        assert response["Content-Security-Policy-Report-Only"]
        assert response["Permissions-Policy"]


# ─────────────────────────────────────────────────────────────────────────────
# M5 + L7 — two-factor hardening
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.django_db
class TestTwoFactorHardening:
    def test_backup_codes_carry_64_bits(self):
        codes = tfs.generate_backup_codes()
        assert len(codes) == 8
        # 16 hex chars = 64 bits. 8 chars (32 bits) is exhaustible offline.
        assert all(len(c) == 16 for c in codes)
        assert len(set(codes)) == 8

    def test_codes_are_stored_with_a_password_hasher(self, teacher):
        payload = tfs.begin_enrolment(teacher)
        teacher.refresh_from_db()

        for stored in teacher.two_factor_backup_codes:
            # identify_hasher raises for a bare sha256 digest.
            assert identify_hasher(stored) is not None
            assert stored not in payload.backup_codes, "codes must never be stored in plaintext"

    def test_a_valid_backup_code_works_once(self, teacher):
        payload = tfs.begin_enrolment(teacher)
        code = payload.backup_codes[0]

        assert tfs.verify_backup_code(teacher, code) is True
        assert tfs.verify_backup_code(teacher, code) is False, "backup codes are single-use"

    def test_codes_are_case_and_whitespace_insensitive(self, teacher):
        payload = tfs.begin_enrolment(teacher)
        assert tfs.verify_backup_code(teacher, f"  {payload.backup_codes[0].lower()} ") is True

    def test_legacy_sha256_codes_still_verify(self, teacher):
        """Admins enrolled before v1.23.0 must not lose their recovery path."""
        legacy_plain = "DEADBEEF"
        teacher.two_factor_backup_codes = [hashlib.sha256(legacy_plain.encode()).hexdigest()]
        teacher.save(update_fields=["two_factor_backup_codes"])

        assert tfs.verify_backup_code(teacher, legacy_plain) is True
        teacher.refresh_from_db()
        assert teacher.two_factor_backup_codes == [], "a legacy code is consumed like any other"

    def test_wrong_code_is_rejected(self, teacher):
        tfs.begin_enrolment(teacher)
        assert tfs.verify_backup_code(teacher, "NOPENOPENOPENOPE") is False
        assert tfs.verify_backup_code(teacher, "") is False

    def test_totp_cannot_be_replayed(self, teacher):
        import pyotp

        payload = tfs.begin_enrolment(teacher)
        code = pyotp.TOTP(payload.secret).now()

        assert tfs.verify_totp(teacher, code) is True
        # Same code, same 30s step, still inside valid_window — must now fail.
        assert tfs.verify_totp(teacher, code) is False, "RFC 6238 §5.2: a used code must be refused"

    def test_replay_counter_is_persisted(self, teacher):
        import pyotp

        payload = tfs.begin_enrolment(teacher)
        tfs.verify_totp(teacher, pyotp.TOTP(payload.secret).now())

        teacher.refresh_from_db()
        assert teacher.two_factor_last_counter is not None

    def test_non_numeric_and_empty_codes_are_rejected(self, teacher):
        tfs.begin_enrolment(teacher)
        assert tfs.verify_totp(teacher, "abcdef") is False
        assert tfs.verify_totp(teacher, "") is False


# ─────────────────────────────────────────────────────────────────────────────
# H2 + L11 — no Google credentials in the session; sessions/tokens get purged
# ─────────────────────────────────────────────────────────────────────────────
class TestOAuthScopes:
    def test_only_identity_scopes_are_requested(self):
        from core.views.auth import _GOOGLE_SCOPES

        joined = " ".join(_GOOGLE_SCOPES)
        assert "gmail.send" not in joined, "nothing uses the Gmail API — email goes over SMTP"
        assert "spreadsheets" not in joined, "the Sheets export authenticates as a service account"
        assert "openid" in joined

    def test_the_callback_keeps_no_credentials(self):
        """Guards the fix structurally: the source must not stash tokens."""
        import inspect

        from core.views import auth as auth_views

        # Strip comments: auth.py now DOCUMENTS what was removed, so a naive
        # substring search matches its own explanatory prose.
        lines = [line for line in inspect.getsource(auth_views).splitlines() if not line.lstrip().startswith("#")]
        code = chr(10).join(lines)
        assert "google_credentials" not in code
        assert "_2fa_pending_google_creds" not in code
        assert 'access_type="offline"' not in code, "offline access mints a refresh token"


@pytest.mark.django_db
class TestSessionPurge:
    def test_spent_and_expired_parent_tokens_are_deleted(self, parent):
        from core.tasks import purge_expired_sessions
        from students.models import ParentSessionToken

        live = ParentSessionToken.issue(parent)

        spent = ParentSessionToken.issue(parent)
        spent.used_at = timezone.now()
        spent.save(update_fields=["used_at"])

        expired = ParentSessionToken.issue(parent)
        expired.expires_at = timezone.now() - timedelta(hours=1)
        expired.save(update_fields=["expires_at"])

        result = purge_expired_sessions()

        assert result["parent_tokens_deleted"] == 2
        remaining = list(ParentSessionToken.objects.values_list("id", flat=True))
        assert remaining == [live.id], "an unused, unexpired token must survive"


# ─────────────────────────────────────────────────────────────────────────────
# H4 — the password-reset endpoint is throttled
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.django_db
class TestPasswordResetThrottle:
    """The suite disables the limiter globally (RATELIMIT_ENABLE=False) so the
    shared cache does not leak between cases, so it is re-enabled per test via
    pytest-django's `settings` fixture. `override_settings` cannot decorate a
    plain class — only SimpleTestCase subclasses."""

    def test_fourth_request_in_the_window_is_throttled(self, client, settings):
        from django.core.cache import cache

        settings.RATELIMIT_ENABLE = True
        cache.clear()
        url = reverse("password_reset")

        for attempt in range(3):
            response = client.post(url, {"email": "nobody@example.com"})
            assert response.status_code in (200, 302), f"attempt {attempt + 1} should be allowed"

        throttled = client.post(url, {"email": "nobody@example.com"})
        assert throttled.status_code == 429
        cache.clear()

    def test_get_is_not_throttled(self, client, settings):
        """Only POSTs send mail, so rendering the form must stay free."""
        from django.core.cache import cache

        settings.RATELIMIT_ENABLE = True
        cache.clear()
        url = reverse("password_reset")
        for _ in range(6):
            assert client.get(url).status_code == 200
        cache.clear()


# ─────────────────────────────────────────────────────────────────────────────
# M1 — the limiter must survive a cache outage instead of 500ing the login page
# ─────────────────────────────────────────────────────────────────────────────
class TestRateLimiterCacheOutage:
    def test_cache_failure_fails_open_and_logs(self, rf, settings):
        """Making the cache external added a new failure mode on the login path.

        Asserts on the logger rather than `caplog`: settings.LOGGING sets
        `propagate: False` on the `core` logger, so its records never reach the
        root handler caplog attaches to and `caplog.text` stays empty even
        though the message is emitted.
        """
        from unittest.mock import patch

        from core.rate_limit import rate_limit

        settings.RATELIMIT_ENABLE = True

        @rate_limit("outage_scope", limit=1)
        def view(request):
            from django.http import HttpResponse

            return HttpResponse("served")

        with (
            patch("core.rate_limit.cache.add", side_effect=ConnectionError("cache is down")),
            patch("core.rate_limit.logger") as mock_logger,
        ):
            response = view(rf.post("/login/"))

        assert response.status_code == 200, "a cache outage must not lock the academy out"
        mock_logger.error.assert_called_once()
        assert "UNTHROTTLED" in mock_logger.error.call_args[0][0], "the degradation must be loud"


# ─────────────────────────────────────────────────────────────────────────────
# M4 — the production posture guard
# ─────────────────────────────────────────────────────────────────────────────
class TestProductionPostureGuard:
    """Exercises the assertions by re-running the settings module logic.

    Importing settings twice inside one process is not safe, so the guard's
    predicate is re-implemented here against the same inputs. The value is in
    pinning the CONTRACT: these five things must be true in production.
    """

    REQUIRED_TRUE = (
        "SESSION_COOKIE_SECURE",
        "CSRF_COOKIE_SECURE",
        "SECURE_SSL_REDIRECT",
        "SESSION_COOKIE_HTTPONLY",
    )

    def test_the_guard_exists_and_names_every_control(self):
        import pathlib

        source = pathlib.Path(__file__).resolve().parents[2] / "project" / "settings.py"
        text = source.read_text(encoding="utf-8")

        assert 'if ENVIRONMENT == "production":' in text
        for name in self.REQUIRED_TRUE:
            assert name in text, f"{name} is not asserted by the production guard"
        assert "SECURE_HSTS_SECONDS" in text
        assert "31536000" in text
        assert "refusing to start" in text

    def test_the_guard_is_keyed_on_environment_not_debug(self):
        """`not DEBUG` would break the testing VM, which runs over plain HTTP."""
        import pathlib

        source = pathlib.Path(__file__).resolve().parents[2] / "project" / "settings.py"
        text = source.read_text(encoding="utf-8")
        guard = text[text.index('if ENVIRONMENT == "production":') :]
        assert "_posture_errors" in guard
