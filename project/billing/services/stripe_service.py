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
        Validate a Stripe webhook signature (t=… v1=… v1=…). Returns True iff:
          - the webhook secret is set (unsigned webhooks are ALWAYS rejected —
            an attacker who reaches the endpoint must not be able to mark
            arbitrary payments completed by guessing session ids), AND
          - the timestamp is within the tolerance window, AND
          - at least one of the `v1` signatures matches the HMAC of
            `f"{timestamp}.{payload}"` against the secret.

        Multiple `v1=` values are supported so Stripe key rotation (which
        signs each event with both the old and new secret for a window) is
        handled correctly.
        """
        if not self.webhook_secret:
            logger.error("STRIPE_WEBHOOK_SECRET is not set — rejecting unsigned webhook")
            return False

        if not sig_header:
            return False

        # Stripe format: "t=123456,v1=abc,v1=def,v0=…"
        timestamp: str | None = None
        received_sigs: list[str] = []
        for item in sig_header.split(","):
            if "=" not in item:
                continue
            k, _, v = item.strip().partition("=")
            if k == "t":
                timestamp = v
            elif k == "v1":
                received_sigs.append(v)

        if not timestamp or not received_sigs:
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

        return any(hmac.compare_digest(expected, sig) for sig in received_sigs)

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
            # Idempotent: Stripe retries webhooks for up to ~3 days after the
            # first 2xx, so a replay must not overwrite the original
            # payment_date or trigger duplicate receipt emails.
            if payment.payment_status == "completed":
                logger.info("Stripe: payment %s already completed, ignoring replay", payment.id)
                return {"status": "already_completed", "payment_id": payment.id}

            payment.payment_status = "completed"
            payment.payment_date = date.today()
            payment.stripe_payment_intent = session.get("payment_intent", "") or ""
            payment.save(update_fields=["payment_status", "payment_date", "stripe_payment_intent", "updated_at"])
            logger.info("Stripe: payment %s marked completed via checkout.session.completed", payment.id)

            # Fire a receipt email so the parent has proof of payment.
            # Late import avoids circular imports at module load.
            try:
                from comms.tasks import send_payment_receipt_email_task

                send_payment_receipt_email_task.delay(payment.id)
            except Exception:  # noqa: BLE001 — email is nice-to-have, never fail the webhook
                logger.exception("Failed to enqueue receipt email for payment %s", payment.id)

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
