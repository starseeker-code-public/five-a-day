"""Integration tests for the Stripe endpoints (v1.11)."""

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _login_parent(client, parent):
    session = client.session
    session["parent_id"] = parent.id
    session.save()


class TestCreateCheckoutLink:
    def test_requires_parent_session(self, client, pending_payment):
        response = client.post(reverse("stripe_create_checkout_link", args=[pending_payment.id]))
        assert response.status_code == 401

    def test_503_when_stripe_unconfigured(self, client, pending_payment):
        _login_parent(client, pending_payment.parent)
        with override_settings(STRIPE_SECRET_KEY=""):
            response = client.post(reverse("stripe_create_checkout_link", args=[pending_payment.id]))
        assert response.status_code == 503

    def test_409_when_payment_already_paid(self, client, completed_payment):
        _login_parent(client, completed_payment.parent)
        with override_settings(STRIPE_SECRET_KEY="sk_test_xxx"):
            response = client.post(reverse("stripe_create_checkout_link", args=[completed_payment.id]))
        assert response.status_code == 409

    def test_success_returns_url(self, client, pending_payment):
        _login_parent(client, pending_payment.parent)
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"id": "cs_test", "url": "https://checkout.example/pay"}
        with override_settings(STRIPE_SECRET_KEY="sk_test_xxx"):
            with patch("billing.services.stripe_service.httpx.post", return_value=fake_response):
                response = client.post(reverse("stripe_create_checkout_link", args=[pending_payment.id]))
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["url"].startswith("https://")

    def test_cross_parent_returns_404(self, client, second_parent, pending_payment):
        # pending_payment belongs to parent A; second_parent must not access it
        _login_parent(client, second_parent)
        with override_settings(STRIPE_SECRET_KEY="sk_test_xxx"):
            response = client.post(reverse("stripe_create_checkout_link", args=[pending_payment.id]))
        assert response.status_code == 404


class TestStripeWebhook:
    def _valid_sig(self, secret, payload_bytes, ts):
        signed = f"{ts}.{payload_bytes.decode()}"
        v1 = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
        return f"t={ts},v1={v1}"

    def test_rejects_invalid_signature(self, client):
        with override_settings(STRIPE_WEBHOOK_SECRET="whsec_test"):
            response = client.post(
                reverse("stripe_webhook"),
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="bogus",
            )
        assert response.status_code == 400

    def test_accepts_valid_signature_and_reconciles(self, client, pending_payment):
        pending_payment.stripe_session_id = "cs_test_ok"
        pending_payment.save()
        secret = "whsec_test"
        payload = json.dumps(
            {
                "type": "checkout.session.completed",
                "data": {"object": {"id": "cs_test_ok", "payment_intent": "pi_1"}},
            }
        ).encode()
        ts = int(time.time())
        sig = self._valid_sig(secret, payload, ts)
        with override_settings(STRIPE_WEBHOOK_SECRET=secret):
            response = client.post(
                reverse("stripe_webhook"),
                data=payload,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=sig,
            )
        assert response.status_code == 200
        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == "completed"

    def test_malformed_json_400(self, client):
        with override_settings(STRIPE_WEBHOOK_SECRET=""):
            response = client.post(
                reverse("stripe_webhook"),
                data=b"not json",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="",
            )
        assert response.status_code == 400
