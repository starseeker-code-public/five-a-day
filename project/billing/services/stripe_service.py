"""
Stripe Checkout integration (v1.11).

Uses direct HTTPS calls (httpx is already a dependency) rather than the
`stripe` SDK — we only need two endpoints (create session, verify webhook)
and skipping the SDK keeps the deploy image small.

The service is dormant until `STRIPE_SECRET_KEY` is set; `is_configured()`
lets callers know whether to render the "Pay now" button.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

_STRIPE_API_BASE = "https://api.stripe.com/v1"


@dataclass
class StripeSession:
    id: str
    url: str


class StripeError(RuntimeError):
    """Raised when Stripe returns a non-2xx response."""


class StripeService:
    def __init__(self):
        self.secret_key = getattr(settings, "STRIPE_SECRET_KEY", "")
        self.webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    def is_configured(self) -> bool:
        return bool(self.secret_key)

    def create_checkout_session(
        self,
        payment,
        success_url: str,
        cancel_url: str,
        customer_email: str | None = None,
    ) -> StripeSession:
        """
        Create a Stripe Checkout session for a single Payment. Amount is in
        cents (integer). Persists `stripe_session_id` on the Payment row so
        the webhook can reconcile inbound events.
        """
        if not self.is_configured():
            raise StripeError("Stripe is not configured (STRIPE_SECRET_KEY missing).")

        amount_cents = int((payment.amount * 100).quantize(0))
        currency = (payment.currency or "EUR").lower()
        description = payment.concept or "Five a Day"

        data = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": str(payment.id),
            "line_items[0][price_data][currency]": currency,
            "line_items[0][price_data][product_data][name]": description,
            "line_items[0][price_data][unit_amount]": str(amount_cents),
            "line_items[0][quantity]": "1",
        }
        if customer_email:
            data["customer_email"] = customer_email

        try:
            response = httpx.post(
                f"{_STRIPE_API_BASE}/checkout/sessions",
                auth=(self.secret_key, ""),
                data=data,
                timeout=15.0,
            )
        except httpx.HTTPError as e:
            raise StripeError(f"Stripe request failed: {e}") from e

        if response.status_code >= 400:
            raise StripeError(f"Stripe returned {response.status_code}: {response.text[:500]}")

        body = response.json()
        session = StripeSession(id=body["id"], url=body["url"])

        payment.stripe_session_id = session.id
        payment.save(update_fields=["stripe_session_id", "updated_at"])

        return session

    def verify_webhook_signature(self, payload: bytes, sig_header: str, tolerance_seconds: int = 300) -> bool:
        """
        Validate a Stripe webhook signature (t=… v1=…). Returns True iff the
        signature is well-formed, within the tolerance window, and matches the
        expected HMAC. Verification is skipped when the secret isn't set — the
        caller should not accept unsigned webhooks in production.
        """
        if not self.webhook_secret:
            logger.warning("STRIPE_WEBHOOK_SECRET is not set — skipping signature verification")
            return True

        try:
            parts = dict(item.split("=", 1) for item in sig_header.split(","))
        except ValueError:
            return False

        timestamp = parts.get("t")
        received_sig = parts.get("v1")
        if not (timestamp and received_sig):
            return False

        try:
            if abs(time.time() - int(timestamp)) > tolerance_seconds:
                return False
        except ValueError:
            return False

        signed = f"{timestamp}.{payload.decode('utf-8')}"
        expected = hmac.new(
            self.webhook_secret.encode("utf-8"),
            signed.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, received_sig)

    def apply_webhook_event(self, event: dict) -> dict:
        """
        Handle a decoded webhook event dict. Currently reconciles two events:
          - checkout.session.completed → mark the linked Payment as completed
          - checkout.session.expired   → wipe the stripe_session_id so a new
                                          link can be issued cleanly
        Anything else is a no-op.
        """
        from datetime import date

        from billing.models import Payment

        event_type = event.get("type", "")
        session = event.get("data", {}).get("object", {})
        session_id = session.get("id", "")

        if not session_id:
            return {"status": "ignored", "reason": "no session id"}

        payment = Payment.objects.filter(stripe_session_id=session_id).first()
        if payment is None:
            return {"status": "ignored", "reason": "no matching payment"}

        if event_type == "checkout.session.completed":
            payment.payment_status = "completed"
            payment.payment_date = date.today()
            payment.stripe_payment_intent = session.get("payment_intent", "") or ""
            payment.save(update_fields=["payment_status", "payment_date", "stripe_payment_intent", "updated_at"])
            logger.info("Stripe: payment %s marked completed via checkout.session.completed", payment.id)
            return {"status": "completed", "payment_id": payment.id}

        if event_type == "checkout.session.expired":
            payment.stripe_session_id = ""
            payment.save(update_fields=["stripe_session_id", "updated_at"])
            return {"status": "expired", "payment_id": payment.id}

        return {"status": "ignored", "reason": f"unhandled event {event_type}"}


def get_stripe_service() -> StripeService:
    return StripeService()


# `json` retained for external decoders that call `json.loads(payload)`
# before invoking `apply_webhook_event`. Ruff can't see that flow so we
# keep the export explicit.
__all__ = [
    "StripeError",
    "StripeService",
    "StripeSession",
    "get_stripe_service",
    "json",
]
