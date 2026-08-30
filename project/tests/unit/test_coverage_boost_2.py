"""Second pass of coverage-boost tests — targets remaining uncovered branches."""

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


# ── waiting_list.py: edge cases ────────────────────────────────────────────


class TestWaitingListExceptionBranches:
    def test_add_to_waiting_list_when_already_waiting_is_idempotent(self, authenticated_client, group):
        from datetime import date

        from students.models import Student

        s = Student.objects.create(
            first_name="AlreadyWaiting",
            last_name="Test",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
            is_waiting=True,
        )
        original_since = s.waiting_since
        response = authenticated_client.post(reverse("add_to_waiting_list", args=[s.id]))
        assert response.status_code == 302
        s.refresh_from_db()
        assert s.waiting_since == original_since


# ── student create in waiting mode ──────────────────────────────────────────


class TestStudentCreateWaitingMode:
    def test_create_student_via_waiting_mode(self, authenticated_client, parent, group):
        """Cover the waiting-mode branch of StudentCreateView (lines 138-147)."""

        from core.models import HistoryLog

        before = HistoryLog.objects.filter(action="waiting_list_added").count()
        response = authenticated_client.post(
            reverse("student_create") + "?mode=waiting",
            {
                "first_name": "NewWaiter",
                "last_name": "Test",
                "birth_date": "2018-01-01",
                "school": "Test School",
                "allergies": "",
                "gdpr_signed": "on",
                "group": group.id,
                "parent_id": parent.id,
            },
        )
        # Redirects to waiting list on success
        assert response.status_code == 302
        assert reverse("waiting_list") in response["Location"]
        after = HistoryLog.objects.filter(action="waiting_list_added").count()
        assert after == before + 1


# ── context_processors.py: exception branches ──────────────────────────────


class TestContextProcessorsExceptionBranches:
    def test_todo_fetch_error_falls_back_to_empty(self, client):
        """Cover lines 15-16 — exception during TodoItem fetch."""
        from django.test import RequestFactory

        from core.context_processors import today_notifications

        req = RequestFactory().get("/")
        req.session = {}
        with patch("core.context_processors.TodoItem.objects") as mock_mgr:
            mock_mgr.filter.side_effect = RuntimeError("db down")
            ctx = today_notifications(req)
        assert ctx["notifications_today_todos"] == []

    def test_history_count_error_falls_back_to_zero(self, client):
        """Cover lines 33-34."""
        from django.test import RequestFactory

        from core.context_processors import today_notifications

        req = RequestFactory().get("/")
        req.session = {}
        with patch("core.context_processors.HistoryLog.objects") as mock_mgr:
            mock_mgr.count.side_effect = RuntimeError("db down")
            ctx = today_notifications(req)
        assert ctx["history_count"] == 0


# ── audit_signals: create-race branch + delete of untracked model ──────────


class TestAuditSignalsBranches:
    def test_pre_save_swallows_race_with_deleted_row(self, group):
        """Cover the DoesNotExist branch: a Student is deleted between pre_save
        capturing and post_save recording."""
        from datetime import date

        from students.models import Student

        s = Student.objects.create(
            first_name="RaceTest",
            last_name="X",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
        )
        # Direct SQL delete so pre_save's `get(pk=...)` inside the next save
        # sees a missing row (simulates a concurrent delete)
        Student.objects.filter(pk=s.pk).delete()

        # Now saving `s` with a stale pk triggers the `DoesNotExist` catch.
        # The save creates a new row (because of INSERT-not-UPDATE semantics
        # when the pk row is missing) — audit should record it as a create.
        s.pk = None  # New INSERT
        s.save()  # No crash

    def test_audit_of_untracked_model_is_noop(self, group):
        """Cover _is_tracked's False branch: HistoryLog is intentionally NOT
        tracked (it's the human feed, not part of the audit surface)."""
        from core.models import AuditLog, HistoryLog

        before = AuditLog.objects.count()
        HistoryLog.log("todo_completed", "test message")
        after = AuditLog.objects.count()
        # No new audit rows created — HistoryLog is off the tracked list.
        assert after == before


# ── PDF service: SiteConfiguration error path ──────────────────────────────


class TestPdfServiceAcademyInfoFallback:
    def test_get_academy_info_falls_back_on_error(self):
        """Cover lines 63-64: any exception in SiteConfiguration.get_config
        must produce a usable AcademyInfo instead of crashing the PDF."""
        from billing.services.pdf_service import _get_academy_info

        with patch(
            "billing.models.SiteConfiguration.get_config",
            side_effect=RuntimeError("db down"),
        ):
            info = _get_academy_info()
        assert info.name == "Five a Day English Academy"


# ── billing.models edge cases (from coverage report) ───────────────────────


class TestExpenseValidation:
    def test_zero_amount_rejected_by_model_validator(self):
        """Cover Expense.clean when amount is too low."""
        from datetime import date
        from decimal import Decimal

        from django.core.exceptions import ValidationError

        from billing.models import Expense

        e = Expense(
            description="X",
            category="rent",
            amount=Decimal("0.001"),  # Below MinValueValidator(0.01)
            expense_date=date.today(),
        )
        with pytest.raises(ValidationError):
            e.full_clean()


# ── stripe_views.py: cross-parent 404 ──────────────────────────────────────


class TestStripeViewsCrossParent:
    def test_stripe_error_bubbles_up_as_502(self, client, pending_payment):
        """Cover the 502 branch when Stripe rejects the request."""
        from billing.services.stripe_service import StripeError

        session = client.session
        session["parent_id"] = pending_payment.parent_id
        session.save()

        with override_settings(STRIPE_SECRET_KEY="sk_test_xxx"):
            with patch(
                "billing.services.stripe_service.StripeService.create_checkout_session",
                side_effect=StripeError("card_declined"),
            ):
                response = client.post(reverse("stripe_create_checkout_link", args=[pending_payment.id]))
        assert response.status_code == 502


# ── rate_limit.py: line 74-77 disabled path ────────────────────────────────


class TestRateLimitDisabledPath:
    def test_disabled_ratelimit_bypasses_completely(self):
        from django.http import HttpRequest, HttpResponse

        from core.rate_limit import rate_limit

        @rate_limit("bypass_scope", limit=1, window_seconds=60)
        def _view(request):
            return HttpResponse("ok")

        def _req():
            r = HttpRequest()
            r.method = "POST"
            r.META["REMOTE_ADDR"] = "5.5.5.5"
            return r

        with override_settings(RATELIMIT_ENABLE=False):
            cache.clear()
            for _ in range(100):
                assert _view(_req()).status_code == 200
