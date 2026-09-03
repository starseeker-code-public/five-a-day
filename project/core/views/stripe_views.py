"""Stripe endpoints — checkout link + webhook receiver (v1.11)."""

from __future__ import annotations

import json
import logging

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from billing.models import Payment
from billing.services.stripe_service import StripeError, get_stripe_service
from core.views.parent_portal import _require_parent

logger = logging.getLogger(__name__)


@require_http_methods(["POST"])
def create_checkout_link(request, payment_id: int):
    """
    Issue a Stripe Checkout URL for a specific pending payment. Only the
    parent that owns the payment (via the parent-portal session) may request
    a link — teachers use bank transfer / cash, not Stripe.
    """
    # Through the SHARED helper, not `session["parent_id"]` raw. Reading the key
    # directly skipped both checks the portal's own pages get:
    #   * the credential stamp — so "changing your password logs out your other
    #     devices" had a hole exactly the shape of this endpoint: a session
    #     invalidated by a password reset could still mint a Checkout URL;
    #   * the must-change-password pin — so a family holding an emailed
    #     TEMPORARY password (a credential sitting in plaintext in an inbox)
    #     could reach a payment page they are otherwise locked out of.
    # The helper's second return value is the redirect it would send a browser;
    # this endpoint answers JSON, so it is discarded for the 401 contract the
    # portal's JS already handles.
    parent, redirect_resp = _require_parent(request)
    if redirect_resp is not None:
        return JsonResponse({"success": False, "error": "not authenticated"}, status=401)

    payment = get_object_or_404(
        Payment.objects.select_related("parent"),
        id=payment_id,
        parent=parent,
    )
    if payment.payment_status == "completed":
        return JsonResponse({"success": False, "error": "already paid"}, status=409)

    service = get_stripe_service()
    if not service.is_configured():
        return JsonResponse(
            {"success": False, "error": "Stripe no está configurado."},
            status=503,
        )

    # Distinct URLs so the parent portal can render a specific toast on each
    # outcome; sharing the same URL means "cancel" and "success" both look
    # identical to the browser.
    base = request.build_absolute_uri(reverse("parent_portal_payments"))
    joiner = "&" if "?" in base else "?"
    success_url = f"{base}{joiner}paid={payment.id}"
    cancel_url = f"{base}{joiner}cancelled={payment.id}"
    try:
        session = service.create_checkout_session(
            payment,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=payment.parent.email if payment.parent else None,
        )
    except StripeError:
        logger.exception("Stripe checkout creation failed for payment=%d", int(payment_id))
        return JsonResponse(
            {"success": False, "error": "No se pudo iniciar el pago con tarjeta."},
            status=502,
        )

    return JsonResponse({"success": True, "url": session.url, "session_id": session.id})


@csrf_exempt
@require_http_methods(["POST"])
def stripe_webhook(request):
    """
    Stripe webhook receiver. Verifies the signature (when configured) and
    delegates reconciliation to the service layer. Always returns 200 for
    events we handled or ignored so Stripe doesn't retry indefinitely; only
    signature failures return non-200.
    """
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    service = get_stripe_service()

    if not service.verify_webhook_signature(payload, sig_header):
        return HttpResponse("invalid signature", status=400)

    try:
        event = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError:
        return HttpResponse("invalid JSON", status=400)

    result = service.apply_webhook_event(event)
    return JsonResponse(result)


__all__ = ["create_checkout_link", "stripe_webhook"]
