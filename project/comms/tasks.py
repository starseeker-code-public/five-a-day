"""
Celery tasks for automated email sending.
These tasks can be scheduled with Celery Beat for periodic execution.

Moved from core/tasks.py as part of the comms app split.

Usage:
    # Send welcome email asynchronously
    send_welcome_email_task.delay(parent_id=1, student_id=2, enrollment_id=3)

    # Manually trigger today's birthday emails
    send_birthday_emails_task.delay()
"""

from datetime import date

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


def _dispatch(task, *args, what: str = "subtask", **kwargs) -> bool:
    """Queue `task` without letting one failure abort the caller's batch.

    **Production has no Celery broker.** `settings.CELERY_TASK_ALWAYS_EAGER` and
    `CELERY_TASK_EAGER_PROPAGATES` are both `not CELERY_BROKER_URL`, so on Cloud
    Run `.delay()` executes the task INLINE and re-raises whatever it raises —
    and `autoretry_for` makes it worse, surfacing as `Retry` once the retry
    budget is spent.

    A bare fan-out loop therefore stops dead on its first bad item. Measured on
    a four-item loop under the production settings: item 0 ran, items 1-3 never
    did. With a broker the "queue each one separately for better error handling"
    comment is true; without one it is exactly backwards.

    Returns True when the item was handled, False when it failed. The caller
    counts and carries on.
    """
    try:
        task.delay(*args, **kwargs)
        return True
    except Exception:
        # Do not log the args — they carry recipient addresses and ids. The
        # task itself logs its own context.
        logger.exception("%s failed and was skipped; the batch continues", what)
        return False


@shared_task(
    name="comms.tasks.send_welcome_email_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
)
def send_welcome_email_task(self, parent_id: int, student_id: int, enrollment_id: int):
    """
    Async task to send a welcome email.
    Triggered when a new student is created.

    Args:
        parent_id: ID of the parent/guardian
        student_id: ID of the student
        enrollment_id: ID of the enrollment
    """
    from billing.models import Enrollment
    from comms.services.email_service import email_service
    from students.models import Parent, Student

    try:
        student = Student.objects.select_related("group").get(id=student_id)
        enrollment = Enrollment.objects.select_related("enrollment_type").get(id=enrollment_id)

        # For adult students, email goes to the student; for children, to the parent
        if parent_id:
            parent = Parent.objects.get(id=parent_id)
            recipient_email = parent.email
            recipient_name = parent.full_name
        else:
            recipient_email = student.email
            recipient_name = student.full_name

        if not recipient_email:
            logger.warning("No email address for welcome email (student_id=%d)", student_id)
            return {"status": "skipped", "message": "No email address"}

        from core.schedule_utils import get_group_schedule_lines

        enrollment_type = enrollment.enrollment_type
        is_special = bool(enrollment_type and enrollment_type.name == "special")

        # Spanish "Mensual" / "Trimestral" — parents were shown the English
        # EnrollmentType label and had no explicit payment frequency. A `special`
        # matrícula is priced and arranged by hand, so its cadence is whatever was
        # agreed with the family: report "Especial" rather than a standard cadence
        # the family never signed up for.
        payment_modality = "Especial" if is_special else enrollment.get_payment_modality_display()

        context = {
            "parent_name": recipient_name,
            "student_name": student.full_name,
            "group_name": student.group.group_name if student.group else None,
            "enrollment_type": enrollment_type.display_name if enrollment_type else None,
            "payment_modality": payment_modality,
            "schedule_type": enrollment.get_schedule_type_display(),
            "schedule_lines": get_group_schedule_lines(student.group),
            # "Fecha de inicio" is when CLASSES start, not when the family signed
            # the enrolment. enrollment_date is the signup day, so a family
            # enrolling in August was told their start date was that same August
            # afternoon rather than the September Monday classes actually begin.
            "start_date": (
                enrollment.enrollment_period_start.strftime("%d/%m/%Y") if enrollment.enrollment_period_start else None
            ),
        }

        success = email_service.send_email(
            template_name="welcome_student",
            recipients=recipient_email,
            subject=f"🎓 ¡Bienvenido/a {student.full_name} a Five a Day!",
            context=context,
        )

        if success:
            logger.info("Welcome email sent for student_id=%d", student_id)
        else:
            logger.error("Failed to send welcome email for student_id=%d", student_id)
            raise RuntimeError("Fallo en el envio del email")

        return {"status": "success", "recipient": recipient_email}

    except (Parent.DoesNotExist, Student.DoesNotExist, Enrollment.DoesNotExist) as e:
        logger.error(
            "Record not found: parent_id=%s student_id=%s enrollment_id=%s", parent_id, student_id, enrollment_id
        )
        return {"status": "error", "message": str(e)}


@shared_task(
    name="comms.tasks.send_birthday_email_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def send_birthday_email_task(self, student_id: int):
    """
    Async task to send a birthday email to a specific student.

    Sends to EVERY parent that has an email — not just the first one. Both
    mom and dad want to know it's their kid's birthday; before this fix the
    task did `.first()` and only picked the arbitrary first row.
    """
    from comms.services.email_service import email_service
    from students.models import Student

    try:
        student = Student.objects.prefetch_related("parents").get(id=student_id)
    except Student.DoesNotExist:
        logger.error("Student not found: id=%d", student_id)
        return {"status": "error", "message": "Student not found"}

    recipients = list(student.parents.exclude(email="").exclude(email__isnull=True).values_list("email", flat=True))
    # Adult students receive the email themselves when no parent is on file.
    if not recipients and student.is_adult and student.email:
        recipients = [student.email]

    if not recipients:
        logger.warning("Student id=%d has no parent (or self) with email", student_id)
        return {"status": "skipped", "reason": "no email recipient"}

    successes = 0
    for email in recipients:
        ok = email_service.send_email(
            template_name="happy_birthday",
            recipients=email,
            subject=f"🎉 ¡Feliz Cumpleaños {student.first_name}!",
            context={"name": student.first_name},
            fail_silently=True,
        )
        if ok:
            successes += 1

    if successes == 0:
        raise RuntimeError("Fallo en el envio del email")

    logger.info("Birthday email sent for student_id=%d to %d recipient(s)", student_id, successes)
    return {"status": "success", "recipients": recipients, "student": student.full_name}


@shared_task(name="comms.tasks.send_birthday_emails_task", bind=True)
def send_birthday_emails_task(self):
    """
    Scheduled task (Celery Beat) that runs daily.
    Finds all students with a birthday today and queues individual emails.

    Configured in celery.py to run at 8:00 AM.
    """
    from django.utils import timezone

    from students.models import Student

    # `date.today()` reads the container's local time (UTC in Docker), which
    # can be the previous day when Celery Beat fires at 08:00 Europe/Madrid
    # in DST — the birthdays we'd send would be for the wrong date. Use
    # `timezone.localdate()` so we always work in `settings.TIME_ZONE`.
    today = timezone.localdate()

    # Find active students with a birthday today
    birthday_students = Student.objects.filter(
        birth_date__month=today.month, birth_date__day=today.day, active=True
    ).values_list("id", flat=True)

    if not birthday_students:
        logger.info("No hay cumpleanos hoy")
        return {"status": "success", "birthdays_found": 0}

    logger.info(f"Encontrados {len(birthday_students)} cumpleanos hoy")

    # One task per student, dispatched through `_dispatch` so a single bad address
    # cannot stop the rest. A bare `.delay()` here aborted the whole loop under
    # production's eager settings: the first failure raised and nobody after it
    # got a birthday email.
    queued = sum(_dispatch(send_birthday_email_task, sid, what="birthday email") for sid in birthday_students)
    failed = len(birthday_students) - queued
    if failed:
        logger.warning("%d de %d emails de cumpleanos no se pudieron enviar", failed, len(birthday_students))

    return {
        "status": "success",
        "birthdays_found": len(birthday_students),
        "tasks_queued": queued,
        "tasks_failed": failed,
    }


@shared_task(name="comms.tasks.send_monthly_report_task", bind=True)
def send_monthly_report_task(self, recipient_email: str | None = None):
    """
    Monthly (day 28) job: email a compact financial snapshot for the current
    month to the support / admin address. When `recipient_email` is None,
    reads `settings.SUPPORT_EMAIL` (which is where every admin-facing
    notification already goes).
    """
    from decimal import Decimal

    from django.conf import settings
    from django.db.models import Case, DecimalField, Sum, Value, When

    from billing.models import Payment
    from comms.services.email_service import email_service

    to = recipient_email or getattr(settings, "SUPPORT_EMAIL", None)
    if not to:
        logger.warning(
            "send_monthly_report_task: SUPPORT_EMAIL is not set — nobody to email; "
            "set SUPPORT_EMAIL in the environment to activate the monthly report."
        )
        return {"status": "skipped", "reason": "no recipient"}

    today = date.today()
    zero = Decimal("0.00")
    stats = Payment.objects.aggregate(
        expected=Sum(
            Case(
                When(
                    due_date__month=today.month,
                    due_date__year=today.year,
                    then="amount",
                ),
                default=Value(0),
                output_field=DecimalField(),
            )
        ),
        # Uses `due_date` consistently (matches `expected` above) so
        # `outstanding = expected - collected` is arithmetic over one
        # population — the previous code mixed `due_date` and `payment_date`
        # and could produce negative "outstanding" when a payment was cashed
        # in a different month from the one it was due in.
        collected=Sum(
            Case(
                When(
                    payment_status="completed",
                    due_date__month=today.month,
                    due_date__year=today.year,
                    then="amount",
                ),
                default=Value(0),
                output_field=DecimalField(),
            )
        ),
    )
    expected = stats["expected"] or zero
    collected = stats["collected"] or zero
    outstanding = expected - collected

    pending_count = Payment.objects.filter(
        payment_status="pending",
        due_date__month=today.month,
        due_date__year=today.year,
    ).count()

    success = email_service.send_email(
        template_name="admin_monthly_report",
        recipients=to,
        subject=f"[Five a Day] Informe mensual {today:%m/%Y}",
        context={
            "period": f"{today:%m/%Y}",
            "expected": f"{expected:.2f}",
            "collected": f"{collected:.2f}",
            "outstanding": f"{outstanding:.2f}",
            "pending_count": pending_count,
        },
        fail_silently=True,
    )
    logger.info("Monthly report sent to %s (email_success=%s)", to, success)
    return {
        "status": "success" if success else "failed",
        "expected": str(expected),
        "collected": str(collected),
        "outstanding": str(outstanding),
        "pending_count": pending_count,
    }


@shared_task(name="comms.tasks.send_payment_reminders", bind=True)
def send_payment_reminders(self):
    """
    Weekly task: Send payment reminders to parents with pending payments.
    Looks for pending payments due within the next 7 days.

    v1.8: also queues an SMS to every parent that has opted in
    (`parent.sms_opt_in=True`) — SMS is a *supplement* to email, not a
    replacement, so both channels fire for opted-in parents.
    """
    from datetime import timedelta

    from billing.models import Payment
    from comms.services.email_service import email_service

    due_date_limit = date.today() + timedelta(days=7)

    pending_payments = Payment.objects.filter(
        payment_status="pending", due_date__lte=due_date_limit, due_date__gte=date.today()
    ).select_related("student", "parent")

    if not pending_payments.exists():
        logger.info("No hay pagos pendientes proximos a vencer")
        return {"status": "no_pending_payments", "sent": 0}

    logger.info(f"Enviando {pending_payments.count()} recordatorios de pago")

    emails_data = []
    # Dedupe SMS by parent so families with several kids only get one SMS
    # per weekly run. The email path keeps one message per child (each
    # email is per-payment, per-student) — the SMS medium is noisier and
    # would look spammy at 3-4 texts in a row.
    sms_parent_ids: set[int] = set()
    sms_payload: list[int] = []
    for payment in pending_payments:
        if payment.parent and payment.parent.email:
            emails_data.append(
                {
                    "recipient": payment.parent.email,
                    "subject": f"Recordatorio de Pago - {payment.student.full_name}",
                    "context": {
                        "student_name": payment.student.full_name,
                        # Decimal — never float for money. The template can
                        # format via {{ amount|floatformat:2 }}.
                        "amount": str(payment.amount),
                        "due_date": payment.due_date.strftime("%d/%m/%Y"),
                    },
                }
            )
        # v1.8: SMS to opted-in parents. SMS is a SUPPLEMENT to email, so the ids
        # are collected here and dispatched *after* the emails go out — see below.
        if payment.parent and payment.parent.sms_opt_in and payment.parent.phone:
            if payment.parent_id not in sms_parent_ids:
                sms_parent_ids.add(payment.parent_id)
                sms_payload.append(payment.id)

    # Use payment_reminder_simple (student_name/amount/due_date context) —
    # the full payment_reminder.html template expects a batch context (IBAN,
    # prices, day-range) that we don't have here, and rendering the per-student
    # reminder with that template used to produce blank fields all through the
    # email body.
    results = email_service.send_bulk_emails(
        template_name="payment_reminder_simple", emails_data=emails_data, fail_silently=True
    )

    # SMS goes out AFTER the email, and through `_dispatch`. This used to be a
    # `.delay()` inside the loop above, which on Cloud Run (no broker, eager +
    # propagate) raised before `send_bulk_emails` was ever reached — so one
    # Twilio failure silently suppressed EVERY payment-reminder email. The
    # comment on that line asserted the opposite.
    sms_sent = sum(_dispatch(send_payment_reminder_sms_task, pid, what="payment-reminder SMS") for pid in sms_payload)
    results["sms_queued"] = sms_sent
    results["sms_failed"] = len(sms_payload) - sms_sent

    logger.info(
        "Recordatorios enviados: %d exitosos, %d fallidos; SMS: %d enviados, %d fallidos",
        results["sent"],
        results["failed"],
        results["sms_queued"],
        results["sms_failed"],
    )
    return results


@shared_task(
    name="comms.tasks.send_payment_reminder_sms_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
)
def send_payment_reminder_sms_task(self, payment_id: int):
    """
    v1.8: send a single-payment SMS reminder to the parent. Skips gracefully
    when the SMS service isn't configured or the parent hasn't opted in.
    """
    from billing.models import Payment
    from comms.services.sms_service import get_sms_service

    try:
        payment = Payment.objects.select_related("student", "parent").get(id=payment_id)
    except Payment.DoesNotExist:
        logger.warning("send_payment_reminder_sms_task: payment %s not found", payment_id)
        return {"status": "error", "message": "payment not found"}

    if not payment.parent:
        return {"status": "skipped", "reason": "no parent"}

    sms = get_sms_service()
    if not sms.is_configured():
        logger.info("SMS service not configured, skipping payment reminder for %s", payment_id)
        return {"status": "skipped", "reason": "sms not configured"}

    body = (
        f"Five a Day: Recordatorio — pago pendiente para {payment.student.full_name} "
        f"({payment.amount:.2f}€, vence {payment.due_date:%d/%m/%Y})."
    )
    result = sms.send_to_parent(payment.parent, body)
    if not result.success:
        logger.warning("SMS reminder failed for payment %s: %s", payment_id, result.error)
    return result.as_dict()


@shared_task(
    name="comms.tasks.send_parent_magic_link_task",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def send_parent_magic_link_task(self, parent_id: int, link: str):
    """
    v1.9 companion task: email the magic-link URL to a parent.

    Runs asynchronously so the /parent/login/ POST returns in constant time
    regardless of SMTP latency — that keeps the enumeration-protection story
    honest (attacker can't distinguish real vs unknown emails by timing).
    """
    from comms.services.email_service import email_service
    from students.models import Parent

    try:
        parent = Parent.objects.get(id=parent_id)
    except Parent.DoesNotExist:
        logger.warning("send_parent_magic_link_task: parent %s not found", parent_id)
        return {"status": "error", "message": "parent not found"}

    if not parent.email:
        return {"status": "skipped", "reason": "no email"}

    success = email_service.send_email(
        template_name="parent_magic_link",
        recipients=parent.email,
        subject="Acceso al portal · Five a Day",
        context={
            "parent_name": parent.first_name,
            "link": link,
            "expires_minutes": 30,
        },
        fail_silently=True,
    )
    return {"status": "success" if success else "failed", "recipient": parent.email}


@shared_task(
    name="comms.tasks.send_payment_receipt_email_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
)
def send_payment_receipt_email_task(self, payment_id: int):
    """
    v1.11 companion task: after Stripe marks a payment completed, email the
    parent a PDF receipt so they have written proof of the transaction.
    Silently skipped when the parent has no email.
    """
    from billing.models import Payment
    from billing.services.pdf_service import generate_payment_receipt
    from comms.services.email_service import email_service

    try:
        payment = Payment.objects.select_related("student", "parent").get(id=payment_id)
    except Payment.DoesNotExist:
        logger.warning("send_payment_receipt_email_task: payment %s not found", payment_id)
        return {"status": "error", "message": "payment not found"}

    recipient = payment.parent.email if payment.parent else payment.student.email
    if not recipient:
        logger.info("send_payment_receipt_email_task: no recipient email for payment %s", payment_id)
        return {"status": "skipped", "reason": "no recipient"}

    try:
        pdf_bytes = generate_payment_receipt(payment)
    except Exception:  # noqa: BLE001 — log and surface, retries will handle transients
        logger.exception("Failed to render PDF for payment %s", payment_id)
        raise

    success = email_service.send_email(
        template_name="payment_receipt",
        recipients=recipient,
        subject=f"Recibo de pago · {payment.student.full_name}",
        context={
            "student_name": payment.student.full_name,
            "amount": str(payment.amount),
            "concept": payment.concept,
            "payment_date": payment.payment_date.strftime("%d/%m/%Y") if payment.payment_date else "",
        },
        attachments=[(f"recibo-{payment.id}.pdf", pdf_bytes, "application/pdf")],
    )
    if not success:
        raise RuntimeError(f"send_email returned False for payment {payment_id}")
    return {"status": "success", "recipient": recipient, "payment_id": payment_id}


@shared_task(
    name="comms.tasks.send_generic_email_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
)
def send_generic_email_task(self, template_name: str, recipient_email: str, subject: str, context: dict = None):
    """
    Generic task to send any type of email.
    Useful for emails that don't have a specific task.

    Args:
        template_name: Template name (without .html)
        recipient_email: Recipient email address
        subject: Email subject
        context: Template variables

    Usage:
        send_generic_email_task.delay(
            template_name='payment_reminder',
            recipient_email='parent@example.com',
            subject='Recordatorio de Pago',
            context={'student_name': 'Juan', 'amount': 100}
        )
    """
    from comms.services.email_service import email_service

    logger.info("Sending email: template=%s", template_name)

    success = email_service.send_email(
        template_name=template_name, recipients=recipient_email, subject=subject, context=context or {}
    )

    if success:
        logger.info("Email sent: template=%s", template_name)
        return {"status": "success"}
    else:
        raise RuntimeError(f"Failed to send email: template={template_name}")


# ============================================================================
# TASK: Enrollment Confirmation (matricula_nino.html)
# ============================================================================


@shared_task(
    name="comms.tasks.send_enrollment_confirmation_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_enrollment_confirmation_task(self, enrollment_id: int, attachments_paths: list = None):
    """
    Send enrollment confirmation email with attached documents.

    Args:
        enrollment_id: ID of the enrollment
        attachments_paths: List of paths to attached PDFs (optional)
    """
    import os

    from billing.models import Enrollment
    from comms.services.email_functions import send_enrollment_confirmation_email

    MONTHS_ES = [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ]

    try:
        enrollment = (
            Enrollment.objects.select_related("student", "student__group")
            .prefetch_related("student__parents")
            .get(id=enrollment_id)
        )

        student = enrollment.student
        parent = student.parents.exclude(email="").exclude(email__isnull=True).first()

        if not parent:
            logger.error("No parent with email for enrollment_id=%d", enrollment_id)
            return {"status": "error", "message": "No parent email"}

        academic_year = enrollment.academic_year

        # Prepare attachments
        attachments = []
        if attachments_paths:
            for path in attachments_paths:
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        attachments.append((os.path.basename(path), f.read(), "application/pdf"))

        success = send_enrollment_confirmation_email(
            parent_email=parent.email,
            student_name=student.full_name,
            gender=student.gender,
            academic_year=academic_year,
            month=MONTHS_ES[enrollment.enrollment_date.month - 1],
            attachments=attachments if attachments else None,
        )

        if success:
            logger.info("Enrollment confirmation sent for enrollment_id=%d", enrollment_id)
            return {"status": "success", "recipient": parent.email}
        else:
            raise RuntimeError("Fallo en envio de confirmacion de matricula")

    except Enrollment.DoesNotExist:
        logger.error("Enrollment not found: id=%d", enrollment_id)
        return {"status": "error", "message": "Enrollment not found"}


@shared_task(name="comms.tasks.send_fun_friday_emails_task", bind=True)
def send_fun_friday_emails_task(
    self,
    recipients: list,
    day_name: str,
    day_number: int,
    month: str,
    start_time: str,
    end_time: str,
    activity_description: str,
    minimum_age=None,
    maximum_age=None,
    meeting_point=None,
):
    """Send the Fun Friday announcement to every recipient immediately.

    Kept for direct/manual sends; the scheduled path persists a
    ``FunFridayScheduledSend`` row that ``send_due_fun_friday_emails_task``
    drains at the right moment (``apply_async(eta=...)`` is NOT used — the
    ETA is silently ignored under ``CELERY_TASK_ALWAYS_EAGER=True``).
    """
    return _send_fun_friday_batch(
        recipients=recipients,
        day_name=day_name,
        day_number=day_number,
        month=month,
        start_time=start_time,
        end_time=end_time,
        activity_description=activity_description,
        minimum_age=minimum_age,
        maximum_age=maximum_age,
        meeting_point=meeting_point,
    )


def _send_fun_friday_batch(
    recipients: list,
    day_name: str,
    day_number: int,
    month: str,
    start_time: str,
    end_time: str,
    activity_description: str,
    minimum_age=None,
    maximum_age=None,
    meeting_point=None,
) -> dict:
    """Send one Fun Friday announcement batch. Shared by the direct task and the drain task."""
    from comms.services.email_functions import send_fun_friday_email

    sent = 0
    for email in recipients:
        try:
            if send_fun_friday_email(
                recipients=email,
                day_name=day_name,
                day_number=day_number,
                month=month,
                start_time=start_time,
                end_time=end_time,
                activity_description=activity_description,
                minimum_age=minimum_age,
                maximum_age=maximum_age,
                meeting_point=meeting_point,
            ):
                sent += 1
        except Exception:  # noqa: BLE001 — one bad recipient must not abort the batch
            logger.exception("Fun Friday email failed for %s", email)

    logger.info("Fun Friday emails sent: %d/%d", sent, len(recipients))
    return {"status": "success", "sent": sent, "total": len(recipients)}


@shared_task(name="comms.tasks.send_due_fun_friday_emails_task", bind=True)
def send_due_fun_friday_emails_task(self):
    """Send every ``FunFridayScheduledSend`` whose scheduled time has passed.

    Idempotent: rows are marked ``sent_at`` and never re-sent. Runs via
    Celery Beat (daily 14:30) in dev/testing and via the
    ``send_due_fun_friday_emails`` management command in production.
    """
    from django.utils import timezone

    from core.models import FunFridayScheduledSend

    due_ids = list(
        FunFridayScheduledSend.objects.filter(sent_at__isnull=True, scheduled_for__lte=timezone.now()).values_list(
            "id", flat=True
        )
    )

    processed = 0
    sent_total = 0
    failed = 0
    for row_id in due_ids:
        # CLAIM the row before sending, with a conditional UPDATE that only
        # matches while sent_at IS NULL. Marking it after the send left a
        # window where two overlapping drains (the immediate .delay() from the
        # form and the scheduled Beat/Cloud Scheduler run, which both fire at
        # 14:30) each saw sent_at=None and mailed every parent twice.
        claimed = FunFridayScheduledSend.objects.filter(id=row_id, sent_at__isnull=True).update(sent_at=timezone.now())
        if not claimed:
            continue  # another worker got there first
        scheduled = FunFridayScheduledSend.objects.get(id=row_id)

        # The row is CLAIMED above, before sending. If the send then raises, the
        # row stays claimed on purpose: `_send_fun_friday_batch` mails parents
        # one at a time and may have delivered some already, so releasing the
        # claim would re-mail them. Losing an announcement is recoverable by
        # hand; sending it twice to every family is not.
        #
        # The `try` is what stops one bad row taking the whole drain down —
        # previously an exception here aborted the loop and every later due row
        # silently went unsent, with its claim already written.
        try:
            result = _send_fun_friday_batch(
                recipients=scheduled.recipients,
                day_name=scheduled.day_name,
                day_number=scheduled.day_number,
                month=scheduled.month,
                start_time=scheduled.start_time,
                end_time=scheduled.end_time,
                activity_description=scheduled.activity_description,
                minimum_age=scheduled.minimum_age,
                maximum_age=scheduled.maximum_age,
                meeting_point=scheduled.meeting_point,
            )
        except Exception:
            failed += 1
            logger.exception(
                "Fun Friday scheduled send %d failed AFTER being claimed — it will not retry. "
                "Re-create it from /apps/ if the announcement still needs to go out.",
                int(row_id),
            )
            continue

        processed += 1
        sent_total += result["sent"]

    if processed or failed:
        logger.info(
            "Fun Friday drain: %d scheduled send(s) processed, %d email(s) sent, %d failed",
            processed,
            sent_total,
            failed,
        )
    return {"status": "success", "processed": processed, "sent": sent_total, "failed": failed}
