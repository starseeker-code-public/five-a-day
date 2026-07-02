"""Unit tests for the Stripe service (v1.11)."""

import hashlib
import hmac
import time
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from billing.services.stripe_service import StripeError, StripeService, get_stripe_service

pytestmark = pytest.mark.django_db


class TestConfiguration:
    def test_unconfigured_by_default(self):
        with override_settings(STRIPE_SECRET_KEY=""):
            assert StripeService().is_configured() is False

    def test_configured_with_secret(self):
        with override_settings(STRIPE_SECRET_KEY="sk_test_xxx"):
            assert StripeService().is_configured() is True


class TestCreateCheckoutSession:
    def test_raises_when_unconfigured(self, pending_payment):
        with override_settings(STRIPE_SECRET_KEY=""):
            with pytest.raises(StripeError):
                StripeService().create_checkout_session(pending_payment, "s", "c")

    def test_success_updates_payment(self, pending_payment):
        with override_settings(STRIPE_SECRET_KEY="sk_test_xxx"):
            fake_response = MagicMock(status_code=200)
            fake_response.json.return_value = {"id": "cs_test_1", "url": "https://checkout.stripe.com/pay/cs_test_1"}
            with patch("billing.services.stripe_service.httpx.post", return_value=fake_response):
                session = StripeService().create_checkout_session(
                    pending_payment,
                    success_url="https://x/success",
                    cancel_url="https://x/cancel",
                    customer_email="a@b.com",
                )
        assert session.id == "cs_test_1"
        pending_payment.refresh_from_db()
        assert pending_payment.stripe_session_id == "cs_test_1"

    def test_stripe_error_bubbles_up(self, pending_payment):
        with override_settings(STRIPE_SECRET_KEY="sk_test_xxx"):
            fake_response = MagicMock(status_code=400, text="bad request")
            with patch("billing.services.stripe_service.httpx.post", return_value=fake_response):
                with pytest.raises(StripeError):
                    StripeService().create_checkout_session(pending_payment, "s", "c")


class TestVerifyWebhookSignature:
    def _build_sig(self, secret: str, payload: bytes, ts: int) -> str:
        signed = f"{ts}.{payload.decode()}"
        v1 = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
        return f"t={ts},v1={v1}"

    def test_missing_secret_skips_verification(self):
        with override_settings(STRIPE_WEBHOOK_SECRET=""):
            assert StripeService().verify_webhook_signature(b"{}", "irrelevant") is True

    def test_valid_signature_accepted(self):
        secret = "whsec_xxx"
        payload = b'{"id":"evt"}'
        ts = int(time.time())
        with override_settings(STRIPE_WEBHOOK_SECRET=secret):
            assert StripeService().verify_webhook_signature(payload, self._build_sig(secret, payload, ts)) is True

    def test_tampered_payload_rejected(self):
        secret = "whsec_xxx"
        payload = b'{"id":"evt"}'
        ts = int(time.time())
        sig = self._build_sig(secret, payload, ts)
        with override_settings(STRIPE_WEBHOOK_SECRET=secret):
            assert StripeService().verify_webhook_signature(b'{"id":"other"}', sig) is False

    def test_expired_timestamp_rejected(self):
        secret = "whsec_xxx"
        payload = b'{"id":"evt"}'
        ts = int(time.time()) - 900  # 15 min ago
        with override_settings(STRIPE_WEBHOOK_SECRET=secret):
            assert StripeService().verify_webhook_signature(payload, self._build_sig(secret, payload, ts)) is False


class TestApplyWebhookEvent:
    def test_completed_marks_payment_paid(self, pending_payment):
        pending_payment.stripe_session_id = "cs_test_1"
        pending_payment.save()
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test_1", "payment_intent": "pi_test_1"}},
        }
        result = StripeService().apply_webhook_event(event)
        assert result["status"] == "completed"
        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == "completed"
        assert pending_payment.stripe_payment_intent == "pi_test_1"

    def test_unknown_session_ignored(self):
        result = StripeService().apply_webhook_event(
            {"type": "checkout.session.completed", "data": {"object": {"id": "cs_unknown"}}}
        )
        assert result["status"] == "ignored"

    def test_expired_clears_session_id(self, pending_payment):
        pending_payment.stripe_session_id = "cs_test_1"
        pending_payment.save()
        result = StripeService().apply_webhook_event(
            {"type": "checkout.session.expired", "data": {"object": {"id": "cs_test_1"}}}
        )
        assert result["status"] == "expired"
        pending_payment.refresh_from_db()
        assert pending_payment.stripe_session_id == ""

    def test_unhandled_event_ignored(self, pending_payment):
        pending_payment.stripe_session_id = "cs_test_1"
        pending_payment.save()
        result = StripeService().apply_webhook_event(
            {"type": "customer.created", "data": {"object": {"id": "cs_test_1"}}}
        )
        assert result["status"] == "ignored"


class TestGetStripeService:
    def test_returns_instance(self):
        assert isinstance(get_stripe_service(), StripeService)
