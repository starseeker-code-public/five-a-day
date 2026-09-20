"""
Email service for Five a Day.
Provides the core EmailService class and configuration helper.

Moved from core/email.py as part of the comms app split.
"""

import contextlib
import logging
import mimetypes
import os
from datetime import date
from email.message import MIMEPart

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string

from comms.log_safe import safe_log

logger = logging.getLogger(__name__)

#: Set once the "allowlist is empty in a non-production environment" warning has
#: been emitted, so it is said at most once per process instead of on every send.
_ALLOWLIST_WARNED = False


def filter_allowed_recipients(addresses: list[str]) -> list[str]:
    """Drop every address `settings.EMAIL_ALLOWED_RECIPIENTS` does not permit.

    An entry is either a full address (`qa@example.com`) or a domain suffix
    beginning with `@` (`@example.com`). An EMPTY allowlist permits everything —
    see the long note in `settings.py` for why that direction, and why the
    containment therefore depends on the environment setting the variable.

    Matched case-insensitively: addresses are typed by hand into `.env` files and
    by admins into the app, and `QA@Example.com` failing to match an allowlisted
    `qa@example.com` would look exactly like the mail silently vanishing.
    """
    global _ALLOWLIST_WARNED

    allowed = getattr(settings, "EMAIL_ALLOWED_RECIPIENTS", None) or []
    if not allowed:
        if not _ALLOWLIST_WARNED and settings.ENVIRONMENT != "production":
            _ALLOWLIST_WARNED = True
            logger.warning(
                "EMAIL_ALLOWED_RECIPIENTS is empty in environment '%s': mail will be "
                "delivered to every address in this database, including seeded ones",
                settings.ENVIRONMENT,
            )
        return list(addresses)

    kept = []
    for address in addresses:
        candidate = (address or "").strip().lower()
        if not candidate:
            continue
        domain = candidate[candidate.rfind("@") :] if "@" in candidate else ""
        if candidate in allowed or (domain and domain in allowed):
            kept.append(address)
    return kept


class EmailService:
    """
    Servicio generico para envio de emails con templates HTML

    Uso:
        email_service = EmailService()
        email_service.send_email(
            template_name='happy_birthday',
            recipients=['user@example.com'],
            context={'name': 'Juan'},
            subject='Feliz Cumpleanos!'
        )
    """

    # NOTE: `LOGO_PATH` / `_get_logo_path()` used to live here and were dead —
    # nothing referenced either, and `core/static/images/logo.png` was never
    # attached to a message. An email image has to be a `cid:` inline part
    # (`inline_images=`, see `send_email`), so a path helper nobody passes to
    # that argument could not render anything. Removed rather than kept "just
    # in case": it read like the logo was already being embedded.

    def __init__(self):
        self.from_email = settings.DEFAULT_FROM_EMAIL
        self.templates_path = "emails/"

    @staticmethod
    def open_connection():
        """A single reusable SMTP connection for a batch of sends.

        Use as a context manager and pass the result to `send_email(...,
        connection=conn)` for every message in the batch, so a mass send opens
        one TCP+TLS+AUTH session instead of one per recipient. Honours
        EMAIL_TIMEOUT, so a stalled server cannot wedge the request forever.

        CAREFUL: opening it FAILS LOUDLY. `with connection:` calls `open()`
        with `fail_silently=False`, so a TCP/TLS/AUTH failure propagates — in a
        request path that is a 500 where the per-message loop would have
        reported "N no pudieron enviarse". Any view-side batch must wrap the
        open in its own try/except; `core.views.app_forms._mass_send` is the one
        place that does it for every mass mail in the app.
        """

        return get_connection()

    def send_email(
        self,
        template_name: str,
        recipients: str | list[str],
        subject: str,
        context: dict | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        fail_silently: bool = False,
        attachments: list | None = None,
        inline_images: dict[str, str] | None = None,
        connection=None,
        reply_to: list[str] | None = None,
    ) -> bool:
        """
        Envia un email usando un template HTML

        Args:
            template_name: Nombre del template (sin .html), ej: 'happy_birthday'
            recipients: Email o lista de emails destinatarios
            subject: Asunto del email
            context: Diccionario con variables para el template
            cc: Lista de emails en copia
            bcc: Lista de emails en copia oculta
            fail_silently: Si True, no lanza excepciones en caso de error
            attachments: Lista de tuplas (filename, content, mimetype)
            inline_images: Dict de {content_id: file_path} para imagenes inline
                           En el template usar: <img src="cid:content_id">
            connection: SMTP connection to REUSE across a batch. Passing one
                        (see `open_connection`) opens a single TCP+TLS+AUTH
                        session for every message in a mass send instead of one
                        per recipient — the difference between N handshakes and 1
                        on the payment-reminder / tax-certificate loops.
            reply_to: Cabecera Reply-To. Por defecto None, que deja el mensaje
                      exactamente como estaba: sin cabecera, y Responder va a
                      `from_email`. Se anadio para el relay del portfolio, donde
                      quien escribe NO es el remitente SMTP — sin esto, pulsar
                      Responder contesta a la cuenta de la academia en vez de a
                      la persona que rellena el formulario.

        Returns:
            True si se envio correctamente, False en caso contrario
        """
        try:
            # Convertir recipient a lista si es string
            if isinstance(recipients, str):
                recipients = [recipients]

            # CONTAINMENT (non-production). Applied HERE, at the single point
            # every send funnels through — `send_bulk_emails` delegates to this
            # method, and so does every convenience function and Celery task —
            # because the sends that most need containing are the ones dispatched
            # to a worker, where no request-scoped guard can reach them.
            #
            # Suppression is reported as SUCCESS (`True`). The caller's question
            # is "did this fail?", and the answer is no: the message was handled
            # exactly as configured. Returning False would make `_mass_send`
            # count it as an error and show the operator a failure banner for a
            # policy that is working, which is how a containment control gets
            # switched off by someone debugging a phantom outage.
            allowed = filter_allowed_recipients(list(recipients))
            if not allowed:
                logger.info(
                    "Email suppressed by EMAIL_ALLOWED_RECIPIENTS: template=%s, %d recipient(s)",
                    safe_log(template_name),
                    len(recipients),
                )
                return True
            if len(allowed) != len(recipients):
                logger.info(
                    "Email partially suppressed by EMAIL_ALLOWED_RECIPIENTS: template=%s, %d of %d kept",
                    safe_log(template_name),
                    len(allowed),
                    len(recipients),
                )
            recipients = allowed
            # cc/bcc carry real addresses too and are filtered by the same rule.
            cc = filter_allowed_recipients(list(cc)) if cc else cc
            bcc = filter_allowed_recipients(list(bcc)) if bcc else bcc

            # Preparar contexto
            if context is None:
                context = {}

            # Anadir variables globales al contexto. `year` used to be hard-
            # coded to 2025, silently backdating every email footer / tax
            # certificate context. Derive from today's date so 2026+ emails
            # render with the current year.
            context.setdefault("year", date.today().year)
            context.setdefault("site_name", "Five a Day")

            # Renderizar template HTML
            template_path = f"{self.templates_path}{template_name}.html"
            html_content = render_to_string(template_path, context)

            # Crear version texto plano (opcional, para clientes sin HTML)
            text_content = f"{subject}\n\nVer este mensaje en un cliente compatible con HTML."

            # Crear email con alternativas (texto y HTML)
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=self.from_email,
                to=recipients,
                cc=cc,
                bcc=bcc,
                connection=connection,
                reply_to=reply_to,
            )
            email.attach_alternative(html_content, "text/html")

            # Anadir imagenes inline si existen (para <img src="cid:...">).
            # Django 6.0 eliminó el atributo `mixed_subtype`; adjuntamos un
            # MIMEPart moderno con Content-ID y Content-Disposition: inline.
            if inline_images:
                for content_id, image_path in inline_images.items():
                    if os.path.exists(image_path):
                        ctype, _ = mimetypes.guess_type(image_path)
                        maintype, _, subtype = (ctype or "image/png").partition("/")
                        with open(image_path, "rb") as img_file:
                            image_part = MIMEPart()
                            image_part.set_content(
                                img_file.read(),
                                maintype=maintype,
                                subtype=subtype,
                                disposition="inline",
                                filename=os.path.basename(image_path),
                            )
                            image_part["Content-ID"] = f"<{content_id}>"
                            email.attach(image_part)

            # Anadir adjuntos si existen
            if attachments:
                for filename, content, mimetype in attachments:
                    email.attach(filename, content, mimetype)

            # Enviar email. `send()` devuelve el nº de mensajes ACEPTADOS por el
            # backend; un fallo de SMTP que el backend se trague devuelve 0 sin
            # lanzar. Descartar ese valor y devolver True siempre hacía que
            # toda la app informara de un envío correcto durante una caída de
            # correo (contadores "0 fallidos", tickets de HistoryLog, el portal
            # diciendo a una familia que revise un buzón al que no llegó nada).
            #
            # `fail_silently` NUNCA baja a Django: es politica de ESTE metodo y
            # se aplica en el `except` de abajo. Pasarlo a `send()` lo instala en
            # el backend, que se traga el motivo dentro de Django (`open()`
            # captura OSError, `_send()` captura SMTPException) y deja un cero
            # pelado sin traza ni causa. Tambien evita el RemovedInDjango70Warning
            # del argumento, deprecado en Django 6.1.
            sent = email.send()
            if not sent:
                # Cero aceptados SIN excepcion: destinatarios vacios, o una
                # conexion compartida construida con fail_silently.
                logger.error(
                    "Email '%s' NO se envio: 0 aceptados por el backend y sin excepcion "
                    "(destinatarios=%d, cc=%d, bcc=%d, conexion_compartida=%s)",
                    safe_log(template_name),
                    len(recipients),
                    len(cc or []),
                    len(bcc or []),
                    connection is not None,
                )
                return False

            logger.info("Email '%s' enviado a %s destinatario(s)", safe_log(template_name), len(recipients))
            return True

        except Exception:
            logger.exception("Error enviando email de plantilla '%s'", safe_log(template_name))
            if not fail_silently:
                raise
            return False

    def send_bulk_emails(
        self, template_name: str, emails_data: list[dict], fail_silently: bool = True
    ) -> dict[str, int]:
        """
        Envia multiples emails usando el mismo template

        Args:
            template_name: Nombre del template
            emails_data: Lista de diccionarios con {recipient, subject, context}
            fail_silently: Si True, continua aunque falle alguno

        Returns:
            Diccionario con {sent: N, failed: N}
        """
        results = {"sent": 0, "failed": 0}

        # One SMTP session for the whole batch (same pattern as the Fun Friday
        # and tax-certificate sends) instead of a TCP+TLS+AUTH handshake per
        # message. The open is guarded because this method's contract is a
        # results dict, never an exception (`open_connection` fails loudly by
        # design): if the server is unreachable we fall back to per-message
        # connections, whose failures are already counted per recipient.
        connection = None
        try:
            connection = self.open_connection()
            connection.open()
        except Exception:
            logger.exception("No se pudo abrir la conexion SMTP compartida; envio individual")
            connection = None

        try:
            for email_data in emails_data:
                success = self.send_email(
                    template_name=template_name,
                    recipients=email_data["recipient"],
                    subject=email_data.get("subject", "Five a Day"),
                    context=email_data.get("context", {}),
                    fail_silently=fail_silently,
                    connection=connection,
                )

                if success:
                    results["sent"] += 1
                else:
                    results["failed"] += 1
                    if connection is not None:
                        # Django's SMTP backend never reopens a connection it
                        # still holds, so ONE mid-batch disconnect (Gmail drops
                        # idle sockets, and enforces a per-session message cap)
                        # turned every remaining send into a guaranteed failure —
                        # 130 payment reminders lost to a socket that died after
                        # the 20th. Drop the shared session on any failure and let
                        # the rest of the batch open their own: a wasted reconnect
                        # after a genuine per-recipient failure (a bad address)
                        # costs one handshake, where guessing wrong the other way
                        # costs the whole run.
                        with contextlib.suppress(Exception):
                            connection.close()
                        connection = None
        finally:
            if connection is not None:
                with contextlib.suppress(Exception):
                    connection.close()

        # A partial mass send used to leave nothing but a count: 130 payment
        # reminders were lost to a dead socket and the only trace was
        # "130 fallidos". The per-message cause now lands in `send_email`, and
        # this line is ERROR when anything failed so the batch itself surfaces
        # (and, in production, alerts) instead of scrolling past at INFO.
        level = logging.ERROR if results["failed"] else logging.INFO
        logger.log(
            level,
            "Envio masivo '%s': %d enviados, %d fallidos de %d",
            safe_log(template_name),
            results["sent"],
            results["failed"],
            len(emails_data),
            extra={
                "email_template": safe_log(template_name),
                "sent": results["sent"],
                "failed": results["failed"],
            },
        )
        return results


# Instancia global del servicio
email_service = EmailService()
