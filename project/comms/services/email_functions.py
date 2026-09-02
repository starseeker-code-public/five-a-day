"""
Convenience email functions for Five a Day.
Each function sends a specific type of email using the shared EmailService.

Moved from core/email.py as part of the comms app split.

NOTE: Templates (emails/*.html) still live in the core templates directory for now.
They should be moved to comms/templates/ in a future step.
"""

import logging
import os

from comms.services.email_service import email_service

logger = logging.getLogger(__name__)


# ============================================================================
# 1. BIRTHDAY - Felicitacion de cumpleanos
# ============================================================================


# ============================================================================
# 2. PAYMENT REMINDER (simple) - Recordatorio de pago pendiente
# ============================================================================


# ============================================================================
# 3. MONTHLY REPORT - Reporte mensual
# ============================================================================


def send_monthly_report(recipient: str, report_data: dict) -> bool:
    """Envia reporte mensual"""
    return email_service.send_email(
        template_name="monthly_report",
        recipients=recipient,
        subject="📊 Reporte Mensual - Five a Day",
        context=report_data,
    )


# ============================================================================
# 4. WELCOME - Bienvenida a nuevo estudiante
# ============================================================================


def send_welcome_email(
    parent_email: str,
    parent_name: str,
    student_name: str,
    group_name: str = None,
    enrollment_type: str = None,
    schedule_type: str = None,
    start_date: str = None,
    schedule_lines: list | None = None,
) -> bool:
    """
    Envia email de bienvenida cuando se matricula un nuevo estudiante.

    Args:
        parent_email: Email del padre/tutor
        parent_name: Nombre del padre/tutor
        student_name: Nombre del estudiante
        group_name: Nombre del grupo asignado
        enrollment_type: Tipo de matricula
        schedule_type: Tipo de horario
        start_date: Fecha de inicio del periodo

    Returns:
        True si se envio correctamente
    """
    return email_service.send_email(
        template_name="welcome_student",
        recipients=parent_email,
        subject=f"🎓 ¡Bienvenido/a {student_name} a Five a Day!",
        context={
            "parent_name": parent_name,
            "student_name": student_name,
            "group_name": group_name,
            "enrollment_type": enrollment_type,
            "schedule_type": schedule_type,
            "schedule_lines": schedule_lines or [],
            "start_date": start_date,
        },
        fail_silently=True,
    )


# ============================================================================
# 5. ENROLLMENT CONFIRMATION - Confirmacion de matricula nino
# ============================================================================


def send_enrollment_confirmation_email(
    parent_email: str, student_name: str, gender: str, academic_year: str, month: str, attachments: list | None = None
) -> bool:
    """
    Envia email de confirmacion de matricula para ninos.

    Args:
        parent_email: Email del padre/tutor
        student_name: Nombre del estudiante
        gender: Genero del estudiante ("m" o "f")
        academic_year: Ano academico (ej: "2024-2025")
        month: Mes de inicio
        attachments: Lista de tuplas (filename, content, mimetype) con PDFs adjuntos

    Returns:
        True si se envio correctamente
    """
    return email_service.send_email(
        template_name="enrollment_child",
        recipients=parent_email,
        subject=f"🎉 Confirmación de Matrícula - {student_name}",
        context={
            "student": student_name,
            "genero": gender,
            "academic_year": academic_year,
            "month": month,
        },
        attachments=attachments,
        fail_silently=True,
    )


# ============================================================================
# 6. FUN FRIDAY - Invitacion a eventos
# ============================================================================


def send_fun_friday_email(
    recipients: str | list[str],
    day_name: str,
    day_number: int,
    month: str,
    start_time: str,
    end_time: str,
    activity_description: str,
    minimum_age: int,
    maximum_age: int,
    meeting_point: str = None,
    event_image_path: str = None,
) -> bool:
    """
    Envia invitacion a evento Fun Friday.

    Args:
        recipients: Email(s) de los padres
        day_name: Nombre del dia (ej: "viernes")
        day_number: Numero del dia
        month: Nombre del mes
        start_time: Hora de inicio (ej: "17:00")
        end_time: Hora de fin (ej: "18:30")
        activity_description: Descripcion de la actividad
        minimum_age: Edad minima
        maximum_age: Edad maxima
        meeting_point: Punto de encuentro (opcional)
        event_image_path: Ruta a imagen del evento (opcional)

    Returns:
        True si se envio correctamente
    """
    inline_images = {}
    if event_image_path and os.path.exists(event_image_path):
        inline_images["event_image"] = event_image_path

    return email_service.send_email(
        template_name="fun_friday",
        recipients=recipients,
        subject=f"🎉 Fun Friday - {day_name.capitalize()} {day_number} de {month}",
        context={
            "day_name": day_name,
            "day_number": day_number,
            "month": month,
            "start_time": start_time,
            "end_time": end_time,
            "activity_description": activity_description,
            "meeting_point": meeting_point,
            "minimum_age": minimum_age,
            "maximum_age": maximum_age,
            # The template guards the `<img cid:event_image>` with
            # `{% if event_image %}`. Set the flag whenever we've actually
            # attached the inline image so the guard passes — otherwise the
            # attachment shipped but the `<img>` was never rendered.
            "event_image": bool(inline_images),
        },
        inline_images=inline_images if inline_images else None,
        fail_silently=True,
    )


# ============================================================================
# 7. PAYMENT REMINDER (full) - Recordatorio de pago mensual/trimestral
# ============================================================================


def send_payment_reminder_email(
    recipients: str | list[str],
    payment_start_day_name: str,
    payment_start_day_number: int,
    payment_end_day_name: str,
    payment_end_day_number: int,
    month: str,
    iban_number: str,
    reduced_price_cheque_idioma: str,
    telephone_number_bizum: str,
    iban_holder: str = "",
    full_time_fee: str | int | None = None,
    part_time_fee: str | int | None = None,
    adult_fee: str | int | None = None,
    quarterly_fee: str | int | None = None,
    sibling_full_time_fee: str | int | None = None,
    attachments: list | None = None,
) -> bool:
    """
    Envia recordatorio de pago mensual/trimestral.

    Args:
        recipients: Email(s) de los padres
        payment_start_day_name: Nombre del dia inicio de pago
        payment_start_day_number: Numero del dia inicio
        payment_end_day_name: Nombre del dia fin de pago
        payment_end_day_number: Numero del dia fin
        month: Mes del pago
        iban_number: Numero IBAN para transferencias
        reduced_price_cheque_idioma: Precio reducido con cheque idioma
        telephone_number_bizum: Telefono para Bizum
        iban_holder: Titular de la cuenta bancaria
        full_time_fee: Cuota 2 sesiones semanales
        part_time_fee: Cuota 1 sesion semanal
        adult_fee: Cuota adultos
        quarterly_fee: Cuota trimestral (3 mensualidades - descuento trimestral)
        sibling_full_time_fee: Cuota 2 sesiones con descuento hermano
        attachments: Lista de PDFs (tarifas, instrucciones)

    Las cinco tarifas se calculan desde SiteConfiguration cuando la llamada no
    las pasa (PricingService.payment_reminder_fees), para que ningun emisor
    mande la tabla de tarifas en blanco.

    Returns:
        True si se envio correctamente
    """
    fees = {
        "full_time_fee": full_time_fee,
        "part_time_fee": part_time_fee,
        "adult_fee": adult_fee,
        "quarterly_fee": quarterly_fee,
        "sibling_full_time_fee": sibling_full_time_fee,
    }
    if any(value is None for value in fees.values()):
        # Only hit SiteConfiguration when the caller left a gap — the batch
        # senders pass all five and loop over every parent.
        from billing.services.pricing_service import PricingService

        defaults = PricingService.payment_reminder_fees()
        fees = {key: (defaults[key] if value is None else value) for key, value in fees.items()}

    return email_service.send_email(
        template_name="payment_reminder",
        recipients=recipients,
        subject=f"💳 Recordatorio de Pago - {month}",
        context={
            "payment_start_day_name": payment_start_day_name,
            "payment_start_day_number": payment_start_day_number,
            "payment_end_day_name": payment_end_day_name,
            "payment_end_day_number": payment_end_day_number,
            "month": month,
            "iban_number": iban_number,
            "iban_holder": iban_holder,
            "reduced_price_cheque_idioma": reduced_price_cheque_idioma,
            "telephone_number_bizum": telephone_number_bizum,
            **fees,
        },
        attachments=attachments,
        fail_silently=True,
    )


# ============================================================================
# 8. QUARTERLY RECEIPT - Recibo trimestral nino
# ============================================================================


def send_quarterly_receipt_email(
    parent_email: str, student_name: str, month_1: str, month_2: str, month_3: str, receipt_pdf: tuple = None
) -> bool:
    """
    Envia recibo trimestral para ninos.

    Args:
        parent_email: Email del padre/tutor
        student_name: Nombre del estudiante
        month_1: Primer mes del trimestre
        month_2: Segundo mes del trimestre
        month_3: Tercer mes del trimestre
        receipt_pdf: Tupla (filename, content, mimetype) con el recibo PDF

    Returns:
        True si se envio correctamente
    """
    attachments = [receipt_pdf] if receipt_pdf else None

    return email_service.send_email(
        template_name="receipt_quarterly_child",
        recipients=parent_email,
        subject=f"🧾 Recibo Trimestral - {student_name}",
        context={
            "student_name": student_name,
            "month_1": month_1,
            "month_2": month_2,
            "month_3": month_3,
        },
        attachments=attachments,
        fail_silently=True,
    )


# ============================================================================
# 9. VACATION CLOSURE - Aviso de cierre por vacaciones
# ============================================================================


def send_vacation_closure_email(
    recipients: str | list[str],
    start_closure_day_name: str,
    start_closure_day_number: int,
    end_closure_day_name: str,
    end_closure_day_number: int,
    month_closure: str,
    closure_reason: str,
    reopening_day_name: str,
    reopening_day_number: int,
    month_reopening: str,
    month_closure_end: str | None = None,
) -> bool:
    """
    Envia aviso de cierre por vacaciones.

    `month_closure_end` es el mes en el que cae el ÚLTIMO día del cierre.
    Se añadió porque los cierres cruzan meses (Navidad: 23 dic → 3 ene),
    y antes solo se pasaba `month_closure` — el email renderizaba
    "hasta el viernes 3 de diciembre" cuando en realidad era 3 de enero.
    Por retrocompatibilidad, si `month_closure_end` es None asumimos que
    el cierre no cruza meses y usamos `month_closure`.
    """
    return email_service.send_email(
        template_name="vacation_closure",
        recipients=recipients,
        subject=f"🏖️ Cierre por {closure_reason} - Five a Day",
        context={
            "start_closure_day_name": start_closure_day_name,
            "start_closure_day_number": start_closure_day_number,
            "end_closure_day_name": end_closure_day_name,
            "end_closure_day_number": end_closure_day_number,
            "month_closure": month_closure,
            "month_closure_end": month_closure_end or month_closure,
            "closure_reason": closure_reason,
            "reopening_day_name": reopening_day_name,
            "reopening_day_number": reopening_day_number,
            "month_reopening": month_reopening,
        },
        fail_silently=True,
    )


# ============================================================================
# 10. TAX CERTIFICATE - Certificado fiscal anual
# ============================================================================


def generate_tax_certificate_pdf(parent, year: int) -> bytes:
    """
    Genera un PDF con el certificado fiscal para la declaracion de la renta.
    Incluye todos los pagos realizados por el padre durante el ano.

    Args:
        parent: Instancia del modelo Parent
        year: Ano fiscal

    Returns:
        Bytes del PDF generado

    Delegates to `billing.services.pdf_service.generate_tax_certificate`,
    which uses reportlab (a hard dependency of the project).
    """
    from billing.services.pdf_service import generate_tax_certificate

    return generate_tax_certificate(parent, year)


def send_tax_certificate_email(parent, year: int) -> bool:
    """
    Genera y envia certificado fiscal para la declaracion de la renta.
    El PDF se genera automaticamente con todos los pagos del ano.

    Args:
        parent: Instancia del modelo Parent (o parent_id como int)
        year: Ano fiscal del certificado

    Returns:
        True si se envio correctamente
    """
    from billing.models import Payment
    from students.models import Parent

    # Si se pasa un ID, obtener el objeto Parent
    if isinstance(parent, int):
        try:
            parent = Parent.objects.get(id=parent)
        except Parent.DoesNotExist:
            logger.error(f"Parent con ID {parent} no encontrado")
            return False

    # Verificar que el padre tiene pagos en ese ano
    payments_count = Payment.objects.filter(parent=parent, payment_status="completed", payment_date__year=year).count()

    if payments_count == 0:
        logger.info("Sin pagos completados en %d para parent_id=%d; no se envia certificado", year, parent.id)
        return False

    # Generar el PDF del certificado. reportlab is a hard dependency, so the
    # returned bytes are always a valid PDF — no more HTML/PDF sniffing needed.
    try:
        pdf_content = generate_tax_certificate_pdf(parent, year)
        certificate_attachment = (
            f"certificado_fiscal_{year}_{parent.dni}.pdf",
            pdf_content,
            "application/pdf",
        )
    except Exception:
        logger.exception("Error generando el certificado fiscal para parent_id=%d", parent.id)
        return False

    return email_service.send_email(
        template_name="tax_certificate",
        recipients=parent.email,
        subject=f"Certificado Fiscal {year} - Five a Day",
        context={
            "year": year,
            "parent_name": parent.full_name,
        },
        attachments=[certificate_attachment],
        fail_silently=True,
    )


def send_all_tax_certificates(year: int) -> dict[str, int]:
    """
    Envia certificados fiscales a TODOS los padres que tengan pagos en el ano.

    Args:
        year: Ano fiscal

    Returns:
        Dict con {sent: N, skipped: N, failed: N}
    """
    from students.models import Parent

    # Obtener todos los padres con pagos completados en ese ano
    parents_with_payments = Parent.objects.filter(
        payments__payment_status="completed", payments__payment_date__year=year
    ).distinct()

    # Log parent_id, never names. These lines go to stdout and therefore into
    # Cloud Logging — a second store with its own retention and its own access
    # list — so logging names duplicates personal data outside the database that
    # AuditLog already deliberately keeps DNI/email/phone out of. comms/tasks.py
    # has always used the `student_id=%d` shape; this file was the exception.
    # The %d coercion also breaks the log-injection taint path (see CLAUDE.md).
    results = {"sent": 0, "skipped": 0, "failed": 0}

    for parent in parents_with_payments:
        if not parent.email:
            logger.warning("parent_id=%d no tiene email; se omite el certificado", parent.id)
            results["skipped"] += 1
            continue

        success = send_tax_certificate_email(parent, year)

        if success:
            results["sent"] += 1
            logger.info("Certificado fiscal enviado a parent_id=%d", parent.id)
        else:
            results["failed"] += 1
            logger.error("Fallo al enviar el certificado fiscal a parent_id=%d", parent.id)

    logger.info(
        f"Certificados fiscales {year}: {results['sent']} enviados, "
        f"{results['skipped']} omitidos, {results['failed']} fallidos"
    )

    return results
