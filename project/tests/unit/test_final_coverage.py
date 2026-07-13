"""Final coverage-boost tests — hits the last few uncovered branches from
the review-pass fixes so my own new code is at or near 100%."""

from unittest.mock import patch

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


# ── waiting_list.py:95-99 — null group defensive branch ────────────────────


class TestAssignWithNullGroupJson:
    def test_ajax_no_parent_returns_400(self, authenticated_client, group, site_config, enrollment_type_monthly):
        """Cover waiting_list.py:122 AJAX-branch for the "no parent + non-adult" guard."""
        from datetime import date

        from students.models import Student

        s = Student.objects.create(
            first_name="OrphanAjax",
            last_name="Test",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
            is_adult=False,
            is_waiting=True,
        )
        response = authenticated_client.post(
            reverse("assign_from_waiting_list", args=[s.id]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "titular" in body["error"]


# ── students.py view:191-198 — on_commit welcome dispatch happy path ───────


class TestWelcomeEmailOnCommitHappyPath:
    def test_successful_create_queues_welcome_task_after_commit(
        self, authenticated_client, parent, group, site_config, enrollment_type_monthly
    ):
        """Happy-path counterpart to the rollback test: when create SUCCEEDS,
        transaction.on_commit fires and the task is delivered exactly once.

        pytest-django wraps each test in a transaction that never commits, so
        on_commit callbacks are queued but never fired. Use
        `TestCase.captureOnCommitCallbacks(execute=True)` to force them.
        """
        from django.test import TestCase

        with patch("comms.tasks.send_welcome_email_task.delay") as mock_task:
            with TestCase.captureOnCommitCallbacks(execute=True):
                response = authenticated_client.post(
                    reverse("student_create"),
                    {
                        "first_name": "OnCommit",
                        "last_name": "Test",
                        "birth_date": "2018-01-01",
                        "school": "S",
                        "allergies": "",
                        "gdpr_signed": "on",
                        "group": group.id,
                        "parent_id": parent.id,
                        "enrollment_plan": "monthly_full",
                    },
                )
        assert response.status_code == 302
        mock_task.assert_called_once()

    def test_welcome_task_delay_error_does_not_break_request(
        self, authenticated_client, parent, group, site_config, enrollment_type_monthly
    ):
        """The inner try/except around .delay() swallows Celery-broker errors."""
        with patch(
            "comms.tasks.send_welcome_email_task.delay",
            side_effect=RuntimeError("broker down"),
        ):
            response = authenticated_client.post(
                reverse("student_create"),
                {
                    "first_name": "BrokerDown",
                    "last_name": "Test",
                    "birth_date": "2018-01-01",
                    "school": "S",
                    "allergies": "",
                    "gdpr_signed": "on",
                    "group": group.id,
                    "parent_id": parent.id,
                    "enrollment_plan": "monthly_full",
                },
            )
        # Even with a broker error the request succeeds — student was created.
        assert response.status_code == 302


# ── stripe_views.py:60-62 — StripeError path (already partly tested, but
#     ensure the httpx path can raise) ────────────────────────────────────


class TestStripeCheckoutHttpxError:
    def test_httpx_error_is_wrapped(self, client, pending_payment):
        import httpx
        from django.test import override_settings

        session = client.session
        session["parent_id"] = pending_payment.parent_id
        session.save()

        with override_settings(STRIPE_SECRET_KEY="sk_test_xxx"):
            with patch(
                "billing.services.stripe_service.httpx.post",
                side_effect=httpx.RequestError("network"),
            ):
                response = client.post(reverse("stripe_create_checkout_link", args=[pending_payment.id]))
        assert response.status_code == 502


# ── comms/tasks.py:340 (SMS task retries autoretry) ─────────────────────────


class TestReceiptEmailTaskPdfError:
    def test_pdf_render_error_reraises_for_retry(self, completed_payment):
        """When generate_payment_receipt raises, the receipt task re-raises so
        Celery's autoretry_for=(Exception,) triggers the retry chain."""
        from comms.tasks import send_payment_receipt_email_task

        with patch(
            "billing.services.pdf_service.generate_payment_receipt",
            side_effect=RuntimeError("reportlab boom"),
        ):
            with pytest.raises(RuntimeError, match="reportlab boom"):
                send_payment_receipt_email_task.run(completed_payment.id)
