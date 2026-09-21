import json
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.decorators import tester_forbidden

logger = logging.getLogger(__name__)

#: The four categories `support.js` can send (its `categoryMap` values), and the
#: Spanish label each one gets in the email. The CLIENT used to supply the
#: display label too, and the raw category went straight into the subject line —
#: Django refuses newlines in headers so it was never header injection, but an
#: arbitrary string in the subject of a mail to SUPPORT_EMAIL is a free-text
#: channel nobody validates. Both now come from here; an unknown key falls back
#: to `exception`, which is what the widget already sends for "Otro".
SUPPORT_CATEGORIES = {
    "frontend": "Interfaz / Problemas visuales",
    "backend": "Sistema / Errores internos",
    "database": "Datos / Base de datos",
    "exception": "Otro",
}
_DEFAULT_CATEGORY = "exception"


@require_http_methods(["POST"])
@tester_forbidden
def submit_support_ticket(request):
    """
    Endpoint API para recibir tickets de soporte.
    Envía un email al SUPPORT_EMAIL con los detalles del ticket.
    """

    try:
        data = json.loads(request.body)

        # Validated against a known set rather than trusted: the value reaches
        # an email subject, and the label reaches the body.
        category = data.get("category", _DEFAULT_CATEGORY)
        if category not in SUPPORT_CATEGORIES:
            logger.info("Support ticket with an unrecognised category; filed as %s", _DEFAULT_CATEGORY)
            category = _DEFAULT_CATEGORY
        category_display = SUPPORT_CATEGORIES[category]
        message = data.get("message", "").strip()
        current_url = data.get("current_url", "/")

        if not message or len(message) < 10:
            return JsonResponse(
                {
                    "success": False,
                    "message": "El mensaje debe tener al menos 10 caracteres",
                },
                status=400,
            )

        username = request.session.get("username", "Anónimo")
        version = settings.APP_VERSION
        now = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")

        support_email = getattr(settings, "SUPPORT_EMAIL", None)

        if not support_email:
            return JsonResponse(
                {"success": False, "message": "Email de soporte no configurado"},
                status=500,
            )

        subject = f"[{category.upper()}] Ticket de Soporte - Five a Day"

        email_body = f"""
═══════════════════════════════════════════════════════════
                    TICKET DE SOPORTE
═══════════════════════════════════════════════════════════

📋 INFORMACIÓN DEL TICKET
───────────────────────────────────────────────────────────
Tipo:           {category} ({category_display})
Versión:        {version}
Fecha/Hora:     {now}
Usuario:        {username}
Vista actual:   {current_url}

💬 MENSAJE
───────────────────────────────────────────────────────────
{message}

═══════════════════════════════════════════════════════════
                    Five a Day - Evolution
═══════════════════════════════════════════════════════════
"""

        send_mail(
            subject=subject,
            message=email_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[support_email],
            fail_silently=False,
        )

        return JsonResponse({"success": True, "message": "Ticket enviado correctamente"})

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Datos inválidos"}, status=400)
    except Exception:
        logger.exception("Error sending support ticket")
        return JsonResponse(
            {"success": False, "message": "Error al enviar el ticket. Inténtalo de nuevo."},
            status=500,
        )
