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

    def test_missing_secret_rejects_all_webhooks(self):
        """After the security fix, an unset STRIPE_WEBHOOK_SECRET must NOT
        bypass signature verification — otherwise an attacker who reaches
        the endpoint can mark arbitrary payments completed."""
        with override_settings(STRIPE_WEBHOOK_SECRET=""):
            assert StripeService().verify_webhook_signature(b"{}", "irrelevant") is False

    def test_rejects_empty_signature_header(self):
        with override_settings(STRIPE_WEBHOOK_SECRET="whsec_xxx"):
            assert StripeService().verify_webhook_signature(b"{}", "") is False

    def test_accepts_multiple_v1_signatures_during_rotation(self):
        """Stripe signs each event with every active secret during rotation;
        the parser must inspect ALL `v1=` entries, not just the last one."""
        secret = "whsec_new"
        payload = b'{"id":"evt"}'
        ts = int(time.time())
        signed = f"{ts}.{payload.decode()}"
        good = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
        # First v1 is nonsense (old key that no longer verifies), second is valid
        sig_header = f"t={ts},v1=deadbeef,v1={good}"
        with override_settings(STRIPE_WEBHOOK_SECRET=secret):
            assert StripeService().verify_webhook_signature(payload, sig_header) is True

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


# A body that is valid bytes but not valid UTF-8 — what the old verifier
# crashed on before it ever reached the signature comparison.
NOT_UTF8 = bytes([0xFF, 0xFE]) + b" not utf-8"


class TestSignatureIsComputedOverRawBytes:
    """The verifier decoded the body to str and re-encoded it — a no-op for the
    UTF-8 Stripe actually sends, but `UnicodeDecodeError` on anything else.
    That was an unhandled 500 on a public, `csrf_exempt` endpoint any
    unauthenticated caller can POST to. Stripe signs the raw bytes, so signing
    them here is also the more faithful implementation.
    """

    @pytest.fixture(autouse=True)
    def _secret(self, settings):
        """Without the secret the verifier short-circuits to False and every
        assertion below would pass for the wrong reason."""
        settings.STRIPE_WEBHOOK_SECRET = "whsec_test"

    def _sign(self, payload: bytes, timestamp: int) -> str:
        digest = hmac.new(b"whsec_test", str(timestamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
        return f"t={timestamp},v1={digest}"

    def test_a_non_utf8_body_is_rejected_rather_than_raising(self):
        header = self._sign(b"{}", int(time.time()))

        assert StripeService().verify_webhook_signature(NOT_UTF8, header) is False

    def test_a_correctly_signed_non_utf8_body_verifies(self):
        payload = NOT_UTF8

        assert StripeService().verify_webhook_signature(payload, self._sign(payload, int(time.time()))) is True

    def test_the_endpoint_answers_400_instead_of_500(self, client):
        from django.urls import reverse

        response = client.post(
            reverse("stripe_webhook"),
            data=NOT_UTF8,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=self._sign(b"{}", int(time.time())),
        )
        assert response.status_code == 400
