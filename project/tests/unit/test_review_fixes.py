"""
Regression tests for the review pass — every fix from the /review + fix
loop has at least one dedicated test here so a future refactor cannot
silently reintroduce the bug.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.urls import reverse

from billing.services.stripe_service import StripeService

pytestmark = pytest.mark.django_db


# ── Stripe: idempotency + receipt email side-effect ─────────────────────────


class TestStripeReplaySafety:
    def test_replay_of_completed_event_is_noop(self, pending_payment):
        """Stripe retries webhooks up to ~3 days after the first 2xx;
        replaying `checkout.session.completed` for an already-paid Payment
        must NOT overwrite payment_date or trigger a duplicate receipt."""
        from datetime import date

        pending_payment.stripe_session_id = "cs_replay"
        pending_payment.payment_status = "completed"
        pending_payment.payment_date = date(2025, 1, 15)  # original charge date
        pending_payment.save()

        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_replay", "payment_intent": "pi_new"}},
        }
        with patch("comms.tasks.send_payment_receipt_email_task.delay") as mock_receipt:
            result = StripeService().apply_webhook_event(event)

        assert result["status"] == "already_completed"
        pending_payment.refresh_from_db()
        assert pending_payment.payment_date.isoformat() == "2025-01-15"
        mock_receipt.assert_not_called()

    def test_first_completed_event_queues_receipt_email(self, pending_payment):
        pending_payment.stripe_session_id = "cs_first"
        pending_payment.save()

        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_first", "payment_intent": "pi_first"}},
        }
        with patch("comms.tasks.send_payment_receipt_email_task.delay") as mock_receipt:
            result = StripeService().apply_webhook_event(event)

        assert result["status"] == "completed"
        mock_receipt.assert_called_once_with(pending_payment.id)

    def test_receipt_email_failure_does_not_break_webhook(self, pending_payment):
        pending_payment.stripe_session_id = "cs_email_fail"
        pending_payment.save()

        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_email_fail"}},
        }
        with patch("comms.tasks.send_payment_receipt_email_task.delay", side_effect=RuntimeError("celery down")):
            result = StripeService().apply_webhook_event(event)

        # Payment is still marked completed even though the email queueing failed.
        assert result["status"] == "completed"
        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == "completed"


# ── Stripe: distinct success/cancel URLs ────────────────────────────────────


class TestStripeCheckoutUrls:
    def test_success_and_cancel_urls_differ(self, client, pending_payment):
        session = client.session
        session["parent_id"] = pending_payment.parent_id
        session.save()

        fake_resp = MagicMock(status_code=200)
        fake_resp.json.return_value = {"id": "cs_xy", "url": "https://checkout.example/pay"}

        captured = {}

        def _capture(url, auth=None, data=None, timeout=None):
            captured.update(data)
            return fake_resp

        with override_settings(STRIPE_SECRET_KEY="sk_test_xxx"):
            with patch("billing.services.stripe_service.httpx.post", side_effect=_capture):
                client.post(reverse("stripe_create_checkout_link", args=[pending_payment.id]))

        assert captured["success_url"] != captured["cancel_url"], (
            "The success and cancel URLs must differ so the browser can distinguish outcomes."
        )
        assert "paid=" in captured["success_url"]
        assert "cancelled=" in captured["cancel_url"]


# ── Portal credential email async + rate limit ─────────────────────────────


class TestPortalCredentialEmailAsync:
    def test_recovery_dispatches_to_celery_not_send_mail_sync(self, client, parent):
        """The synchronous send_mail call is gone — verify the task is queued
        instead so an SMTP hang can't stall the request (and can't be timed to
        tell a registered address from an unknown one)."""
        with patch("comms.tasks.send_parent_temporary_password_task.delay") as mock_delay:
            response = client.post(reverse("parent_portal_forgot_password"), {"email": parent.email})
        assert response.status_code == 200
        mock_delay.assert_called_once()
        args = mock_delay.call_args[0]
        # First positional arg is parent_id
        assert args[0] == parent.id
        # Second is the absolute login URL. The PASSWORD is deliberately absent:
        # it is generated inside the task so the plaintext never reaches the
        # broker or Celery's task log.
        assert "/parent/login/" in args[1]
        assert not any("password" in str(a).lower() for a in args[2:])

    def test_change_password_is_rate_limited(self, client, parent):
        """`parent_portal_change_password` takes the CURRENT password in its
        voluntary mode, so the POST is throttled like the staff equivalent —
        otherwise a session cookie plus unlimited attempts is a password oracle.

        Only the POST counts. `rate_limit` defaults to POST-only for good
        reason: rendering the form is not an attempt, and charging quota for it
        would let a family lock themselves out by refreshing the page.
        """
        parent.set_portal_password("Portal-Fam-2026")
        session = client.session
        session["parent_id"] = parent.id
        # The credential stamp the session was opened with — a password change
        # bumps it and invalidates every session that predates it, so a
        # hand-built session must carry the current value to be accepted.
        from core.views.parent_portal import _PARENT_CRED_STAMP_KEY, _credential_stamp

        session[_PARENT_CRED_STAMP_KEY] = _credential_stamp(parent)
        session.save()
        payload = {
            "current_password": "wrong",
            "password": "Otra-Clave-2026",
            "password_confirm": "Otra-Clave-2026",
        }

        with override_settings(RATELIMIT_ENABLE=True):
            from django.core.cache import cache

            cache.clear()
            for _ in range(5):
                assert client.post(reverse("parent_portal_change_password"), payload).status_code == 200
            assert client.post(reverse("parent_portal_change_password"), payload).status_code == 429
            # GETs are not charged, so the form still renders while throttled.
            assert client.get(reverse("parent_portal_change_password")).status_code == 200


# ── Rate limiter ────────────────────────────────────────────────────────────


class TestRateLimitCountMethods:
    def test_get_can_be_counted(self):
        from django.core.cache import cache
        from django.http import HttpRequest, HttpResponse

        from core.rate_limit import rate_limit

        @rate_limit("get_test_scope", limit=2, window_seconds=60, count_methods=("GET",))
        def _view(request):
            return HttpResponse("ok")

        def _req():
            r = HttpRequest()
            r.method = "GET"
            r.META["REMOTE_ADDR"] = "9.9.9.9"
            return r

        with override_settings(RATELIMIT_ENABLE=True):
            cache.clear()
            assert _view(_req()).status_code == 200
            assert _view(_req()).status_code == 200
            assert _view(_req()).status_code == 429
            cache.clear()

    def test_post_not_counted_when_only_get_is_configured(self):
        from django.core.cache import cache
        from django.http import HttpRequest, HttpResponse

        from core.rate_limit import rate_limit

        @rate_limit("post_bypass_scope", limit=1, window_seconds=60, count_methods=("GET",))
        def _view(request):
            return HttpResponse("ok")

        def _req():
            r = HttpRequest()
            r.method = "POST"
            r.META["REMOTE_ADDR"] = "9.9.9.10"
            return r

        with override_settings(RATELIMIT_ENABLE=True):
            cache.clear()
            for _ in range(5):
                assert _view(_req()).status_code == 200


# ── Waiting-list guards ─────────────────────────────────────────────────────


class TestWaitingListAssignGuards:
    def test_waiting_entry_without_parent_goes_through_the_full_creation_flow(self, authenticated_client, group):
        """A waiting entry has no parent/tutor, so it can't be promoted in place.

        The old shortcut created the enrollment (and a payment with no titular)
        straight from the list. Now the button hands over to the normal
        "Matricular" flow, which asks for the padre/tutor first.
        """
        from datetime import date

        from students.models import Student

        s = Student.objects.create(
            first_name="Orphan",
            last_name="Waiter",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
            is_adult=False,
            is_waiting=True,
        )
        response = authenticated_client.get(reverse("assign_from_waiting_list", args=[s.id]))
        assert response.status_code == 302
        assert response.url == f"{reverse('parent_create')}?from_waiting={s.id}"
        s.refresh_from_db()
        assert s.is_waiting is True  # Still on the waiting list
        assert not s.enrollments.exists()


# ── Payment reminders: SMS dedupe + Decimal ─────────────────────────────────


class TestPaymentReminderDedupe:
    def test_sms_deduped_by_parent(self, group, parent, enrollment_type_new_student, site_config):
        """Parents with several kids should only get one SMS per weekly run."""
        from datetime import date, timedelta
        from decimal import Decimal

        from billing.models import Enrollment, Payment
        from comms.tasks import send_payment_reminders
        from students.models import Student, StudentParent

        parent.sms_opt_in = True
        parent.phone = "+34600111222"
        parent.save()

        # Two kids belonging to the same parent, each with a pending payment
        kids = []
        for name in ("Alpha", "Beta"):
            child = Student.objects.create(
                first_name=name,
                last_name="Sibling",
                birth_date=date(2018, 1, 1),
                gdpr_signed=True,
                group=group,
            )
            StudentParent.objects.create(student=child, parent=parent)
            kids.append(child)

        due = date.today() + timedelta(days=3)
        for child in kids:
            enrollment = Enrollment.objects.create(
                student=child,
                enrollment_type=enrollment_type_new_student,
                enrollment_period_start=date.today(),
                enrollment_period_end=date.today() + timedelta(days=365),
                academic_year="2025-2026",
                schedule_type="full_time",
                payment_modality="monthly",
                enrollment_amount=Decimal("54.00"),
                final_amount=Decimal("54.00"),
                status="active",
                enrollment_date=date.today(),
            )
            Payment.objects.create(
                student=child,
                parent=parent,
                enrollment=enrollment,
                payment_type="monthly",
                payment_method="transfer",
                amount=Decimal("54.00"),
                payment_status="pending",
                due_date=due,
                concept="Mensualidad",
            )

        with patch("comms.tasks.send_payment_reminder_sms_task.delay") as mock_sms:
            with patch(
                "comms.services.email_service.EmailService.send_bulk_emails", return_value={"sent": 2, "failed": 0}
            ):
                result = send_payment_reminders.run()

        # Two email reminders (one per kid) but only ONE SMS (deduped by parent)
        assert mock_sms.call_count == 1
        assert result["sms_queued"] == 1

    def test_reminder_context_uses_string_amount_not_float(self, pending_payment):
        """Money in Decimal, never float. The reminder email context used to
        do `float(payment.amount)` which loses precision."""
        from datetime import date, timedelta

        # Make the payment "due within 7 days" so the reminder task picks it up.
        pending_payment.parent.sms_opt_in = False
        pending_payment.parent.save()
        pending_payment.due_date = date.today() + timedelta(days=3)
        pending_payment.save()

        from comms.tasks import send_payment_reminders

        captured = {}

        def _capture_bulk(template_name, emails_data, fail_silently=True):
            for item in emails_data:
                captured.update(item.get("context", {}))
            return {"sent": len(emails_data), "failed": 0}

        with patch("comms.services.email_service.EmailService.send_bulk_emails", side_effect=_capture_bulk):
            send_payment_reminders.run()

        assert "amount" in captured
        # Amount is a string (str(Decimal)), not a float — the template can
        # apply |floatformat as needed but the value never lost precision.
        assert isinstance(captured["amount"], str)


# ── Audit log: PII allow-list ───────────────────────────────────────────────


class TestAuditLogPii:
    def test_parent_update_does_not_log_dni_or_email(self, parent):
        """After the fix, Parent audit rows must not persist DNI or email —
        GDPR-sensitive PII was previously written into the JSON payload."""
        from core.models import AuditLog

        parent.first_name = "Renamed"
        parent.email = "changed@example.com"
        parent.dni = "99999999X"
        parent.save()

        log = AuditLog.objects.filter(model="students.Parent", action="update").latest("id")
        # Only tracked fields (first_name here) may show up
        assert "first_name" in log.changes
        assert "email" not in log.changes
        assert "dni" not in log.changes
        assert "phone" not in log.changes

    def test_teacher_update_does_not_log_email(self, teacher):
        from core.models import AuditLog

        teacher.first_name = "Renamed"
        teacher.email = "new@example.com"
        teacher.save()

        log = AuditLog.objects.filter(model="students.Teacher", action="update").latest("id")
        assert "first_name" in log.changes
        assert "email" not in log.changes

    def test_payment_amount_serialises_as_string(self, pending_payment):
        """Decimal amounts must be JSON-serialised as strings (not floats
        that lose precision)."""
        from decimal import Decimal

        from core.models import AuditLog

        pending_payment.amount = Decimal("99.99")
        pending_payment.save()

        log = AuditLog.objects.filter(model="billing.Payment", action="update").latest("id")
        # Old value / new value both strings
        assert isinstance(log.changes["amount"][1], str)
        assert log.changes["amount"][1] == "99.99"


# ── PWA service worker: no session-scoped caching ───────────────────────────


class TestPwaCacheExclusions:
    def test_dashboard_paths_not_in_cacheable_list(self, client):
        """After the fix, `/`, `/students/`, `/payments/` are no longer
        auto-cached by the service worker — those responses are user-scoped
        and caching them leaks session data on shared devices."""
        response = client.get(reverse("service_worker"))
        body = response.content.decode()
        # STATIC_SHELL is now the cache-precache list; it must contain no
        # authenticated route.
        assert "STATIC_SHELL" in body
        # Explicitly ensure the old shell URLs are gone
        import re

        static_shell = re.search(r"const STATIC_SHELL = \[(.*?)\];", body, re.DOTALL).group(1)
        assert '"/",' not in static_shell
        assert '"/students/",' not in static_shell
        assert '"/payments/",' not in static_shell

    def test_isCacheable_scoping(self, client):
        response = client.get(reverse("service_worker"))
        body = response.content.decode()
        assert "function isCacheable" in body
        # Only session-free paths are cacheable.
        assert '"/static/"' in body
        assert '"/manifest.webmanifest"' in body

    def test_login_page_is_not_cached(self, client):
        """/login/ must NOT be cache-first.

        It looks public, but it carries a CSRF token and Django rotates the CSRF
        secret on login — a cached copy hands back a token minted against the
        old secret and the next sign-in 403s. The Cache API ignores
        Cache-Control, so the server's `no-store` cannot prevent it.
        """
        body = client.get(reverse("service_worker")).content.decode()
        cacheable = body.split("function isCacheable")[1].split("}")[0]
        assert 'path === "/login/"' not in cacheable


# ── Report PDF service extraction ───────────────────────────────────────────


class TestReportPdfInService:
    def test_service_function_returns_pdf(self):
        from billing.services.pdf_service import generate_report_pdf

        stub_report = {
            "current_month": {
                "income": 100.0,
                "pending": 10.0,
                "expenses": 20.0,
                "net": 80.0,
            },
            "collection": {"expected": 100.0, "collected": 90.0, "percent": "90.00"},
            "retention": {"baseline": 5, "still_active": 4, "retention_percent": "80.00"},
            "groups": [],
        }
        pdf = generate_report_pdf(stub_report, 3, 2026)
        assert pdf.startswith(b"%PDF-")

    def test_reports_view_rejects_out_of_range_month(self, authenticated_client):
        # Month 99 falls back to the current month — the same `safe_int`
        # behaviour every other view uses. It used to CLAMP to 12 here (its own
        # third copy of the helper), so /reports/?month=99 showed December while
        # /payments/?month=99 showed the current month.
        from datetime import date

        response = authenticated_client.get(reverse("reports_view"), {"month": 99, "year": 2026})
        assert response.status_code == 200
        assert response.context["month"] == date.today().month
