"""Authentication / authorization hardening (v1.27.1).

Every test here pins a fix for a bug that was verified reachable, and each one
names the failure it prevents. Grouped by the thing being defended, not by
module.
"""

import json
import logging
import re
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.middleware import (
    NON_ADMIN_ALLOWED_URL_NAMES,
    QAErrorEmailMiddleware,
    RedactingExceptionReporterFilter,
    SimpleAuthMiddleware,
    _is_non_admin_teacher,
    _session_identity_revoked,
)
from students.models import Teacher

User = get_user_model()
pytestmark = pytest.mark.django_db


def _normalised_body(response, *echoed: str) -> bytes:
    """Response body with per-request randomness (and any echoed input) removed.

    Two renders of the same page are never byte-identical, for two reasons that
    say nothing about the account: `csp_nonce` is regenerated per request, and
    so is the CSRF token. A raw `.content` comparison therefore only ever tests
    those two values and always fails.

    `echoed` normalises values the form legitimately echoes back — the address
    that was just typed. That is attacker-supplied and already known to
    whoever submitted it, so it is not a signal about whether the account
    exists; what must not differ is everything else.

    Comparing the whole normalised body rather than just the flash messages is
    deliberate: it keeps any OTHER divergence in scope, which is the point of
    an enumeration test.
    """
    body = re.sub(rb'nonce="[^"]*"', b'nonce="N"', response.content)
    body = re.sub(rb'(csrfmiddlewaretoken"\s+value=")[^"]*(")', rb"\1T\2", body)
    for value in echoed:
        body = body.replace(value.encode(), b"ECHOED")
    return body


def _make_teacher(email="hard@fiveaday.test", password="teach-pw", admin=False):
    teacher = Teacher.objects.create(first_name="Hard", last_name="Teacher", email=email, admin=admin)
    teacher.ensure_user(password=password)
    return teacher


def _logged_in(client, teacher):
    """Both layers, exactly as a real login leaves them."""
    client.force_login(teacher.user)
    session = client.session
    session["is_authenticated"] = True
    session["username"] = teacher.first_name
    session.save()
    return client


# ─────────────────────────────────────────────────────────────────────────────
# 1 — an offboarded teacher's live session must not escape the whitelist
# ─────────────────────────────────────────────────────────────────────────────
class TestOffboardedSessionIsRejected:
    """Layer 1 checked only `session["is_authenticated"]`, which nothing
    revokes. Deactivating the auth.User, deactivating the Teacher, or deleting
    the Teacher all make `request.user` AnonymousUser — and
    `_is_non_admin_teacher` then answered "not a non-admin", handing the session
    the UNRESTRICTED set. Revocation ESCALATED privileges, for up to 6 h of
    rolling session expiry.
    """

    def test_deactivating_the_teacher_ends_the_session(self, client):
        teacher = _make_teacher(email="off1@fiveaday.test")
        _logged_in(client, teacher)
        assert client.get("/students/").status_code == 200

        teacher.active = False
        teacher.save()

        response = client.get("/students/")
        assert response.status_code == 302
        assert response.url == reverse("login")
        assert not client.session.get("is_authenticated"), "the session must be flushed, not merely redirected"

    def test_an_offboarded_session_cannot_reach_the_financial_endpoints(self, client):
        """The escalation, stated as the thing that actually mattered."""
        teacher = _make_teacher(email="off2@fiveaday.test")
        _logged_in(client, teacher)
        teacher.active = False
        teacher.save()

        for path in ("/payments/", "/database/", "/management/", "/expenses/"):
            response = client.get(path)
            assert response.status_code == 302, path
            assert response.url == reverse("login"), path

    def test_deleting_the_teacher_ends_the_session(self, client):
        teacher = _make_teacher(email="off3@fiveaday.test")
        _logged_in(client, teacher)
        teacher.delete()

        response = client.get("/students/")
        assert response.status_code == 302
        assert response.url == reverse("login")

    def test_deactivating_the_auth_user_ends_the_session(self, client):
        teacher = _make_teacher(email="off4@fiveaday.test")
        _logged_in(client, teacher)
        User.objects.filter(pk=teacher.user_id).update(is_active=False)

        assert client.get("/students/").url == reverse("login")

    def test_a_superuser_with_no_teacher_still_works(self, client):
        """The dev env-var login and every OAuth session are free-standing
        superusers with no Teacher row. Both escape hatches are deliberate."""
        user = User.objects.create_superuser("devbox", "dev@example.com", "pw")
        client.force_login(user)
        session = client.session
        session["is_authenticated"] = True
        session.save()

        assert client.get("/payments/").status_code == 200

    def test_an_active_admin_teacher_is_untouched(self, client):
        teacher = _make_teacher(email="ok@fiveaday.test", admin=True)
        _logged_in(client, teacher)
        assert client.get("/payments/").status_code == 200

    def test_the_legacy_session_shape_is_left_alone(self, client):
        """A session flagged authenticated with NO Django identity cannot be
        produced by any login path (they all call django.contrib.auth.login) and
        cannot be forged (sessions are server-side). It is the test suite's
        `authenticated_client` shape, so it is explicitly not rejected."""
        session = client.session
        session["is_authenticated"] = True
        session.save()

        assert _session_identity_revoked_for(client) is False
        assert client.get("/payments/").status_code == 200


def _session_identity_revoked_for(client):
    from django.test import RequestFactory

    request = RequestFactory().get("/payments/")
    request.session = client.session
    return _session_identity_revoked(request)


class TestNonAdminDeterminationFailsClosed:
    """An unresolvable identity must get the RESTRICTED set, never the
    unrestricted one."""

    def _request(self, user=None, session=None):
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        request = RequestFactory().get("/payments/")
        request.user = user if user is not None else AnonymousUser()
        request.session = session if session is not None else {}
        return request

    def test_a_claimed_but_unresolvable_identity_is_restricted(self):
        from django.contrib.auth import SESSION_KEY

        request = self._request(session={SESSION_KEY: "7", "is_authenticated": True})
        assert _is_non_admin_teacher(request) is True

    def test_an_inactive_admin_teacher_is_restricted(self, client):
        """Reading `admin` off a deactivated row would keep granting the full
        set to somebody who has been let go."""
        from django.test import RequestFactory

        teacher = _make_teacher(email="inact@fiveaday.test", admin=True)
        teacher.active = False
        teacher.save()

        request = RequestFactory().get("/payments/")
        request.user = User.objects.get(pk=teacher.user_id)
        request.session = {}
        assert _is_non_admin_teacher(request) is True


# ─────────────────────────────────────────────────────────────────────────────
# 2 — the QA error email must not mail a new password
# ─────────────────────────────────────────────────────────────────────────────
class TestRedactionCoversEveryPostedSecret:
    #: Every credential-bearing field name posted anywhere in the app.
    POSTED_SECRET_FIELDS = (
        "password",  # /login/, /parent/login/, /parent/change-password/
        "new_password1",  # /password-reset/confirm/
        "new_password2",
        "current_password",  # /api/password-change/, portal change form
        "new_password",
        "confirm_password",  # /api/password-change/ — WAS MISSING
        "password_confirm",  # portal change form
        "old_password",  # django admin password change
        "password1",
        "password2",
        "code",  # all three 2FA views
        "csrfmiddlewaretoken",
        "dni",
        "iban",
    )

    @pytest.mark.parametrize("field", POSTED_SECRET_FIELDS)
    def test_urlencoded(self, field):
        assert "hunter2" not in QAErrorEmailMiddleware._redact_body(f"{field}=hunter2")

    @pytest.mark.parametrize("field", POSTED_SECRET_FIELDS)
    def test_json(self, field):
        body = '{"' + field + '": "hunter2"}'
        assert "hunter2" not in QAErrorEmailMiddleware._redact_body(body)

    @pytest.mark.parametrize("field", POSTED_SECRET_FIELDS)
    def test_multipart(self, field):
        crlf = chr(13) + chr(10)
        body = crlf.join(
            [
                "--B",
                f'Content-Disposition: form-data; name="{field}"',
                "",
                "hunter2",
                "--B--",
                "",
            ]
        )
        assert "hunter2" not in QAErrorEmailMiddleware._redact_body(body)

    def test_the_change_password_payload_leaks_nothing(self):
        """The exact JSON `/api/password-change/` posts. `confirm_password`
        carried the same plaintext as `new_password`, so a 500 here mailed the
        new staff password to SUPPORT_EMAIL."""
        body = '{"current_password": "old-one", "new_password": "N3w-Passw0rd!", "confirm_password": "N3w-Passw0rd!"}'
        out = QAErrorEmailMiddleware._redact_body(body)
        assert "N3w-Passw0rd!" not in out
        assert "old-one" not in out


class TestExceptionReporterRedaction:
    """`AdminEmailHandler` (production alerting) renders the request body, and
    Django only cleanses it when the view used `@sensitive_post_parameters` —
    which none of this app's hand-rolled auth views do."""

    def test_post_secrets_are_cleansed(self):
        from django.test import RequestFactory

        request = RequestFactory().post("/login/", data={"username": "ana", "password": "hunter2", "code": "123456"})
        cleansed = RedactingExceptionReporterFilter().get_post_parameters(request)

        assert "hunter2" not in str(cleansed)
        assert "123456" not in str(cleansed)
        assert cleansed["username"] == "ana", "non-credential fields stay readable"

    def test_the_live_request_is_not_mutated(self):
        from django.test import RequestFactory

        request = RequestFactory().post("/login/", data={"password": "hunter2"})
        RedactingExceptionReporterFilter().get_post_parameters(request)
        assert request.POST["password"] == "hunter2"


# ─────────────────────────────────────────────────────────────────────────────
# 3 — Stripe checkout goes through the shared portal helper
# ─────────────────────────────────────────────────────────────────────────────
class TestStripeCheckoutUsesTheSharedGuard:
    def test_a_session_invalidated_by_a_password_change_cannot_mint_a_link(self, client, pending_payment):
        """Reading `session["parent_id"]` raw skipped the credential stamp, so
        "resetting your password logs out your other devices" had a hole exactly
        the shape of this endpoint."""
        parent = pending_payment.parent
        session = client.session
        session["parent_id"] = parent.id
        session["parent_credential_stamp"] = ""
        session.save()

        parent.set_portal_password("Portal-Fam-2026")  # bumps portal_credential_changed_at

        response = client.post(reverse("stripe_create_checkout_link", args=[pending_payment.id]))
        assert response.status_code == 401

    def test_a_family_on_a_temporary_password_cannot_mint_a_link(self, client, pending_payment):
        """The must-change-password pin: the credential in play is sitting in
        plaintext in an inbox, so the portal stays shut until it is replaced."""
        parent = pending_payment.parent
        session = client.session
        session["parent_id"] = parent.id
        session["parent_must_change_password"] = True
        session.save()

        response = client.post(reverse("stripe_create_checkout_link", args=[pending_payment.id]))
        assert response.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 4 — the portal is protected by DEFAULT
# ─────────────────────────────────────────────────────────────────────────────
class TestPortalUrlsAreNotBlanketPublic:
    def test_the_blanket_prefix_is_gone(self):
        assert "/parent/" not in SimpleAuthMiddleware.PUBLIC_PREFIXES

    def test_only_login_and_recovery_are_public(self):
        assert SimpleAuthMiddleware.PORTAL_PUBLIC_URL_NAMES == {
            "parent_portal_login",
            "parent_portal_forgot_password",
        }

    @pytest.mark.parametrize(
        "url_name",
        ["parent_portal_dashboard", "parent_portal_payments", "parent_portal_tax_certificate"],
    )
    def test_a_portal_page_without_a_session_is_bounced_by_the_middleware(self, client, url_name):
        response = client.get(reverse(url_name))
        assert response.status_code == 302
        assert response.url == reverse("parent_portal_login")

    def test_the_public_pages_still_load(self, client):
        assert client.get(reverse("parent_portal_login")).status_code == 200
        assert client.get(reverse("parent_portal_forgot_password")).status_code == 200

    def test_a_json_portal_endpoint_answers_401_not_a_redirect(self, client, pending_payment):
        response = client.post(reverse("stripe_create_checkout_link", args=[pending_payment.id]))
        assert response.status_code == 401

    def test_a_logged_in_family_reaches_the_dashboard(self, client, parent):
        session = client.session
        session["parent_id"] = parent.id
        session.save()
        assert client.get(reverse("parent_portal_dashboard")).status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 5 — the recovery endpoint
# ─────────────────────────────────────────────────────────────────────────────
class TestRecoveryCannotBeUsedToDenyRecovery:
    """Each hit used to ROTATE the family's temporary password, so an
    unauthenticated attacker replaying the form kept the credential in that
    family's inbox permanently stale — a denial of the recovery path itself.
    """

    def _post(self, client, email):
        return client.post(reverse("parent_portal_forgot_password"), {"email": email})

    def test_a_replay_inside_the_cooldown_keeps_the_live_credential(self, client, parent):
        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as send:
            self._post(client, parent.email)
            first = send.call_args[1]["context"]["temporary_password"]
            self._post(client, parent.email)

        parent.refresh_from_db()
        assert parent.authenticate_portal(first) is not None, (
            "the password already in the family's mailbox must still work"
        )
        assert send.call_count == 1, "the replay must not send a second email either"

    def test_the_response_is_identical_for_a_replay(self, client, parent):
        first = self._post(client, parent.email)
        second = self._post(client, parent.email)
        assert first.status_code == second.status_code == 200
        assert _normalised_body(first) == _normalised_body(second)

    def test_a_genuine_second_request_after_the_cooldown_reissues(self, client, parent):
        from core.views.parent_portal import PORTAL_TEMPORARY_PASSWORD_COOLDOWN

        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as send:
            self._post(client, parent.email)
            first = send.call_args[1]["context"]["temporary_password"]

            parent.refresh_from_db()
            parent.temporary_password_issued_at = timezone.now() - (PORTAL_TEMPORARY_PASSWORD_COOLDOWN * 2)
            parent.save(update_fields=["temporary_password_issued_at"])

            self._post(client, parent.email)
            second = send.call_args[1]["context"]["temporary_password"]

        assert first != second
        parent.refresh_from_db()
        assert parent.authenticate_portal(first) is None, "the superseded password must stop working"
        assert parent.authenticate_portal(second) is not None

    def test_an_admin_reissue_ignores_the_cooldown(self, client, parent, rf):
        """An admin on the phone with a family must be able to reissue now."""
        from core.views.parent_portal import send_portal_temporary_password

        request = rf.get("/admin/")
        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as send:
            assert send_portal_temporary_password(request, parent, reset=True) is True
            first = send.call_args[1]["context"]["temporary_password"]
            parent.refresh_from_db()
            assert send_portal_temporary_password(request, parent, reset=True) is True
            second = send.call_args[1]["context"]["temporary_password"]

        assert first != second

    def test_the_limit_matches_the_staff_password_reset(self):
        """3 / 15 min, not 5 / 60 s. Both endpoints are unauthenticated, both
        send mail, and both spend the SAME Gmail daily quota — exhaust it and
        payment reminders, receipts and welcome mail all stop silently.

        Asserted on the source because `RATELIMIT_ENABLE` is False in the test
        settings, so the decorator cannot be exercised end to end here.
        """
        import inspect

        from core.views import parent_portal

        source = inspect.getsource(parent_portal)
        assert 'rate_limit("parent_portal_forgot", limit=3, window_seconds=900)' in source


class TestRecoveryTimingIsNotAnEnumerationOracle:
    """Production runs CELERY_TASK_ALWAYS_EAGER (Cloud Run, no broker), so
    `.delay()` executed the task INLINE — a PBKDF2 hash plus a live SMTP round
    trip — for a known address and nothing but two dummy hashes for an unknown
    one. Response latency was a clean oracle for which addresses belong to
    families of the academy.
    """

    def test_the_send_happens_after_the_response_is_written(self):
        """`response.close()` is called by the WSGI server AFTER the body has
        been sent (PEP 3333), and by Django's test client for the same reason."""
        from django.http import HttpResponse

        from core.views.parent_portal import _run_after_response_sent

        ran = []
        response = HttpResponse("body")
        _run_after_response_sent(response, lambda: ran.append(1))

        assert ran == [], "must not run while the response is being built"
        response.close()
        assert ran == [1]
        response.close()
        assert ran == [1], "and exactly once"

    def test_a_failure_after_the_response_cannot_500_it(self):
        from django.http import HttpResponse

        from core.views.parent_portal import _run_after_response_sent

        response = HttpResponse("body")
        _run_after_response_sent(response, lambda: 1 / 0)
        response.close()  # must not raise

    def test_both_branches_burn_the_same_hashing_work(self, client, parent):
        """The dummy-hash equalisation is kept and EXTENDED: it is now paid by
        the known branch too, so neither can be identified by how long the POST
        took."""
        with patch("students.models.burn_portal_login_work") as burn:
            client.post(reverse("parent_portal_forgot_password"), {"email": parent.email})
            known = burn.call_count
            client.post(reverse("parent_portal_forgot_password"), {"email": "nobody@example.com"})
            unknown = burn.call_count - known

        assert known == unknown == 1

    def test_the_body_is_identical_for_a_known_and_an_unknown_address(self, client, parent):
        unknown_email = "nobody@example.com"
        known = client.post(reverse("parent_portal_forgot_password"), {"email": parent.email})
        unknown = client.post(reverse("parent_portal_forgot_password"), {"email": unknown_email})
        assert _normalised_body(known, parent.email, unknown_email) == _normalised_body(
            unknown, parent.email, unknown_email
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6 / 7 — the forced change-password flow
# ─────────────────────────────────────────────────────────────────────────────
class TestForcedChangePasswordCost:
    """It called `authenticate_portal` merely to ask "is this the temporary
    password", and that helper always runs the own-password branch first — the
    real hash, or a deliberate dummy burn. Two PBKDF2 verifications (~0.6 s of
    CPU) per POST, retries included, on an AUTHENTICATED flow every newly
    invited family passes through.
    """

    def _forced_session(self, client, parent):
        session = client.session
        session["parent_id"] = parent.id
        session["parent_must_change_password"] = True
        session.save()

    def test_no_authenticate_portal_call_is_made(self, client, parent):
        parent.issue_temporary_password()
        self._forced_session(client, parent)

        with patch.object(type(parent), "authenticate_portal", autospec=True) as auth:
            response = client.post(
                reverse("parent_portal_change_password"),
                {"password": "Nueva-Clave-9", "password_confirm": "Nueva-Clave-9"},
            )

        assert response.status_code == 302
        assert auth.call_count == 0

    def test_reusing_the_temporary_password_is_still_refused(self, client, parent):
        raw = parent.issue_temporary_password()
        self._forced_session(client, parent)

        response = client.post(
            reverse("parent_portal_change_password"),
            {"password": raw, "password_confirm": raw},
        )

        assert response.status_code == 200
        parent.refresh_from_db()
        assert not parent.has_portal_password, "the temporary password must not become the permanent one"

    def test_a_parent_with_no_temporary_password_can_still_finish(self, client, parent):
        """`parent.temporary_password` is empty for a session forced by other
        means; the guard must short-circuit rather than crash."""
        self._forced_session(client, parent)

        response = client.post(
            reverse("parent_portal_change_password"),
            {"password": "Nueva-Clave-9", "password_confirm": "Nueva-Clave-9"},
        )

        assert response.status_code == 302
        parent.refresh_from_db()
        assert parent.has_portal_password


class TestSessionStartTakesTheParent:
    def test_the_session_is_always_stamped(self, client, parent):
        """`_start_parent_session` re-fetched the row its caller already held,
        and the dead `is not None` branch could open a STAMPLESS session."""
        parent.set_portal_password("Portal-Fam-2026")
        client.post(reverse("parent_portal_login"), {"email": parent.email, "password": "Portal-Fam-2026"})

        parent.refresh_from_db()
        assert client.session["parent_credential_stamp"] == parent.portal_credential_changed_at.isoformat()

    def test_it_no_longer_accepts_an_id(self, parent):
        import inspect

        from core.views.parent_portal import _start_parent_session

        signature = inspect.signature(_start_parent_session)
        assert "parent_id" not in signature.parameters
        assert "parent" in signature.parameters


# ─────────────────────────────────────────────────────────────────────────────
# 8 — logout is POST only
# ─────────────────────────────────────────────────────────────────────────────
class TestLogoutRequiresPost:
    """A logout on GET is one-click CSRF, and a prefetching browser or a link
    scanner can trigger it by accident."""

    def test_staff_logout_rejects_get(self, client):
        teacher = _make_teacher(email="lo@fiveaday.test", admin=True)
        _logged_in(client, teacher)

        assert client.get(reverse("logout")).status_code == 405
        assert client.session.get("is_authenticated") is True

        assert client.post(reverse("logout")).status_code == 302
        assert not client.session.get("is_authenticated")
        assert not client.session.get("_auth_user_id")

    def test_portal_logout_rejects_get(self, client, parent):
        session = client.session
        session["parent_id"] = parent.id
        session.save()

        assert client.get(reverse("parent_portal_logout")).status_code == 405
        assert client.session.get("parent_id") == parent.id

        assert client.post(reverse("parent_portal_logout")).status_code == 302
        assert client.session.get("parent_id") is None


# ─────────────────────────────────────────────────────────────────────────────
# 9 — per-view authorization + the non-admin ledger
# ─────────────────────────────────────────────────────────────────────────────
class TestAdminRequiredDecorator:
    """The middleware allowlist was the SOLE control on every financial write
    endpoint, so one missing entry was a silent privilege grant and nothing at
    the view said who may call it."""

    @staticmethod
    def _view(request):
        from django.http import HttpResponse

        return HttpResponse("ok")

    def _wrapped(self):
        from core.decorators import admin_required

        return admin_required(self._view)

    def _request(self, path, client=None):
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        request = RequestFactory().get(path)
        request.user = AnonymousUser()
        request.session = {} if client is None else client.session
        return request

    def test_an_unauthenticated_caller_is_sent_to_login(self):
        response = self._wrapped()(self._request("/payments/"))
        assert response.status_code == 302
        assert response.url == reverse("login")

    def test_an_unauthenticated_api_caller_gets_403_json(self):
        response = self._wrapped()(self._request("/api/config/update/"))
        assert response.status_code == 403
        # The decorator is invoked directly here, so this is a bare
        # JsonResponse — `.json()` is a helper the test CLIENT adds to the
        # responses it returns, not a method on the response class itself.
        assert json.loads(response.content)["success"] is False

    def test_a_non_admin_teacher_is_refused(self, rf):
        teacher = _make_teacher(email="dec@fiveaday.test", admin=False)
        request = rf.get("/api/config/update/")
        request.user = User.objects.get(pk=teacher.user_id)
        request.session = {"is_authenticated": True}

        response = self._wrapped()(request)
        assert response.status_code == 403

    def test_an_admin_teacher_is_allowed(self, rf):
        teacher = _make_teacher(email="dec2@fiveaday.test", admin=True)
        request = rf.get("/payments/")
        request.user = User.objects.get(pk=teacher.user_id)
        request.session = {"is_authenticated": True}

        assert self._wrapped()(request).status_code == 200


class TestIsAdminUserFailsClosed:
    def test_an_anonymous_render_is_not_an_admin(self, client):
        """`not is_non_admin_teacher` answered True for a request with no
        session at all, so any page rendered to an anonymous visitor claimed
        admin in its context."""
        from django.test import RequestFactory

        from core.context_processors import today_notifications

        request = RequestFactory().get("/login/")
        request.session = {}
        context = today_notifications(request)

        assert context["is_admin_user"] is False

    def test_a_logged_in_admin_still_is_one(self, client):
        teacher = _make_teacher(email="ctx@fiveaday.test", admin=True)
        _logged_in(client, teacher)
        assert client.get("/").context["is_admin_user"] is True


class TestNonAdminLedger:
    """The P&L is not a teaching tool. `expenses_list` renders every rent,
    salary and software line for the month plus the totals — exactly the class
    of figure the trimmed non-admin dashboard exists to withhold — and
    `create_expense` is a financial write against it."""

    @pytest.fixture
    def non_admin_client(self, client):
        return _logged_in(client, _make_teacher(email="led@fiveaday.test", admin=False))

    @pytest.mark.parametrize("url_name", ["expenses_list", "create_expense"])
    def test_expenses_are_not_in_the_whitelist(self, url_name):
        assert url_name not in NON_ADMIN_ALLOWED_URL_NAMES

    def test_a_non_admin_cannot_read_the_expense_ledger(self, non_admin_client):
        response = non_admin_client.get(reverse("expenses_list"))
        assert response.status_code == 302
        assert response.url == reverse("home")

    def test_a_non_admin_cannot_write_an_expense(self, non_admin_client):
        response = non_admin_client.post(reverse("create_expense"), {"description": "x", "amount": "10"})
        assert response.status_code == 302

    def test_the_ficha_is_still_reachable(self, non_admin_client, student):
        """Kept deliberately: the roll is this role's core surface. The payment
        history it renders is gated in the TEMPLATE on `is_admin_user`."""
        assert non_admin_client.get(reverse("student_detail", args=[student.id])).status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 10 / 11 / 12 — settings posture
# ─────────────────────────────────────────────────────────────────────────────
class TestSettingsPosture:
    @staticmethod
    def _source():
        import pathlib

        return (pathlib.Path(__file__).resolve().parents[2] / "project" / "settings.py").read_text(encoding="utf-8")

    def test_the_production_guard_asserts_the_cache_backend(self):
        """Dropping CACHE_DB falls back to a per-process LocMemCache and
        multiplies every rate limit by workers x instances."""
        guard = self._source()
        guard = guard[guard.index('if ENVIRONMENT == "production":') :]
        assert "locmem" in guard
        assert "CACHE_DB" in guard

    def test_the_guard_is_still_keyed_on_environment(self):
        """`not DEBUG` would break the testing VM, which runs plain HTTP."""
        source = self._source()
        assert 'if ENVIRONMENT == "production":' in source
        assert "if not DEBUG:\n    _posture_errors" not in source

    def test_both_database_branches_cap_the_connect_time(self):
        """`statement_timeout` bounds a statement on an ESTABLISHED connection
        and says nothing about a TCP connect that never completes."""
        source = self._source()
        assert source.count("connect_timeout") >= 2

    def test_dead_email_secret_setting_is_gone(self):
        source = self._source()
        assert "\nEMAIL_SECRET = " not in source
        assert 'os.getenv("EMAIL_SECRET", "")' in source, "the env var is still the Gmail app password"

    def test_error_mail_is_configured_but_inert_outside_production(self, settings):
        assert "mail_admins" in settings.LOGGING["handlers"]
        assert "mail_admins" not in settings.LOGGING["root"]["handlers"], (
            "an alert in the test suite would land in mail.outbox and break every message count"
        )

    def test_the_reporter_filter_is_wired_up(self, settings):
        assert settings.DEFAULT_EXCEPTION_REPORTER_FILTER == "core.middleware.RedactingExceptionReporterFilter"


class TestErrorAlertThrottle:
    """`AdminEmailHandler` has no throttle. One 500 in a loop mutes the inbox
    within a day, and alerting believed to work is worse than none."""

    def _record(self, line=10, name="core.views.payments"):
        return logging.LogRecord(name, logging.ERROR, "/app/x.py", line, "boom %s", ("x",), None)

    def test_the_first_alert_passes(self):
        from core.rate_limit import ErrorAlertThrottleFilter

        assert ErrorAlertThrottleFilter().filter(self._record()) is True

    def test_a_repeat_from_the_same_site_is_suppressed(self):
        from core.rate_limit import ErrorAlertThrottleFilter

        throttle = ErrorAlertThrottleFilter()
        assert throttle.filter(self._record()) is True
        assert throttle.filter(self._record()) is False
        assert throttle.filter(self._record()) is False

    def test_a_different_site_is_not_suppressed(self):
        from core.rate_limit import ErrorAlertThrottleFilter

        throttle = ErrorAlertThrottleFilter()
        throttle.filter(self._record(line=10))
        assert throttle.filter(self._record(line=99)) is True

    def test_the_message_text_cannot_defeat_it(self):
        """Keyed on the call SITE, not the formatted text — a per-request id or
        a student name in the message is exactly what spams."""
        from core.rate_limit import ErrorAlertThrottleFilter

        throttle = ErrorAlertThrottleFilter()
        first = self._record()
        second = self._record()
        second.args = ("a completely different value",)
        assert throttle.filter(first) is True
        assert throttle.filter(second) is False

    def test_the_bookkeeping_is_bounded(self):
        from core.rate_limit import ErrorAlertThrottleFilter

        throttle = ErrorAlertThrottleFilter()
        for line in range(ErrorAlertThrottleFilter.MAX_TRACKED * 2):
            throttle.filter(self._record(line=line))
        assert len(throttle._seen) <= ErrorAlertThrottleFilter.MAX_TRACKED


def test_a_client_can_still_log_in_and_out_end_to_end(client):
    """Belt and braces over the whole of item 1 + item 8: the ordinary flow must
    survive every check added above."""
    _make_teacher(email="e2e@fiveaday.test", password="e2e-pass-1234", admin=True)
    with patch("core.views.auth._is_dev_env", return_value=False):
        response = client.post(reverse("login"), {"username": "e2e@fiveaday.test", "password": "e2e-pass-1234"})
    assert response.url == reverse("home")
    assert client.get("/payments/").status_code == 200
    assert client.post(reverse("logout")).status_code == 302
    assert client.get("/payments/").url == reverse("login")


def test_a_fresh_client_is_unaffected_by_the_portal_gate(client):
    """`/parents/create/` must not be caught by the `/parent/` prefix."""
    assert Client().get("/parents/create/").url == reverse("login")
