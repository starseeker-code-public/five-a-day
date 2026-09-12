"""
Comando Django para enviar emails
Uso:
    python manage.py send_email --template happy_birthday --recipient user@example.com --subject "Feliz Cumpleaños"
    python manage.py send_email --template monthly_report --test
    python manage.py send_email --fun-friday --activity "Zumba en el parque" --date 2025-01-10 --time 17:00-18:30
    python manage.py send_email --payment-reminder --month enero
    python manage.py send_email --vacation-closure --reason "Navidad" --start 2025-12-23 --end 2025-01-07 --reopen 2025-01-08
    python manage.py send_email --tax-certificate --year 2024
    python manage.py send_email --tax-certificate --year 2024 --recipient email
"""

import json
from datetime import date, datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Prefetch

from comms.services.email_functions import (
    cheque_idioma_fee,
    send_all_tax_certificates,
    send_fun_friday_email,
    send_payment_reminder_email,
    send_quarterly_receipt_email,
    send_tax_certificate_email,
    send_vacation_closure_email,
)
from comms.services.email_service import email_service
from core.constants import DIAS_ES, MESES_ES
from students.models import Parent, Student


def _mass_mail_parent_filters() -> dict:
    """The filters every mass-mail recipient query in this command must carry.

    Mirrors `core.views.app_forms._recipient_filters`: `is_waiting=False` is the
    one that was missing. A waiting-list entry taken over the phone has no
    `Parent` row, but a student MOVED BACK onto the list keeps their parents —
    so `children__active=True` alone mailed families whose child is not
    enrolled. Duplicated rather than imported because a management command in
    `comms` importing `core.views` would be a layering inversion; the two are
    two lines each and the docstring names its twin.
    """
    return {"children__active": True, "children__is_waiting": False}


def _dedupe_emails(addresses) -> list[str]:
    """Collapse addresses differing only by case/whitespace, keeping order.

    `Parent.email` is not unique, so a couple sharing a mailbox is two rows and
    got two copies of every batch.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in addresses:
        address = (raw or "").strip()
        if address and address.lower() not in seen:
            seen.add(address.lower())
            ordered.append(address)
    return ordered


def _mass_mail_recipients() -> list[str]:
    """Deduplicated addresses of the parents of currently enrolled students."""
    return _dedupe_emails(
        Parent.objects.filter(**_mass_mail_parent_filters())
        .exclude(email="")
        .exclude(email__isnull=True)
        .order_by("id")
        .values_list("email", flat=True)
    )


class Command(BaseCommand):
    help = "Envía emails usando templates del sistema"

    def add_arguments(self, parser):
        parser.add_argument("--template", type=str, required=True, help="Nombre del template")
        parser.add_argument("--recipient", type=str, help="Email del destinatario")
        parser.add_argument("--subject", type=str, help="Asunto del email")
        parser.add_argument("--context", type=str, help="JSON con variables del template")
        parser.add_argument("--test", action="store_true", help="Envía email de prueba")
        parser.add_argument("--birthdays", action="store_true", help="Envía cumpleaños de hoy")
        parser.add_argument("--monthly-reports", action="store_true", help="Envía reportes mensuales")
        parser.add_argument("--fun-friday", action="store_true", help="Envía Fun Friday")
        parser.add_argument("--activity", type=str, help="Descripción actividad Fun Friday")
        parser.add_argument("--date", type=str, help="Fecha evento (YYYY-MM-DD)")
        parser.add_argument("--time", type=str, help="Horario (HH:MM-HH:MM)")
        parser.add_argument("--meeting-point", type=str, help="Punto de encuentro")
        parser.add_argument("--min-age", type=int, default=4, help="Edad mínima")
        parser.add_argument("--max-age", type=int, default=12, help="Edad máxima")
        parser.add_argument("--event-image", type=str, help="Ruta a imagen del evento")
        parser.add_argument("--payment-reminder", action="store_true", help="Recordatorio de pago")
        parser.add_argument("--month", type=str, help="Mes del pago")
        parser.add_argument("--payment-start", type=str, default="1", help="Día inicio pago")
        parser.add_argument("--payment-end", type=str, default="5", help="Día fin pago")
        # Defaults are None so the real sources (SiteConfiguration / env) are used
        # when the flag is omitted. The old literal defaults shipped a PLACEHOLDER
        # IBAN nobody could pay into and a cheque-idioma price of 40€ where the app
        # derives 34€ — argparse always populates options, so the `.get(..., "")`
        # fallbacks in the send code never fired.
        parser.add_argument("--iban", type=str, default=None)
        parser.add_argument("--cheque-idioma-price", type=str, default=None)
        parser.add_argument("--bizum-phone", type=str, default=None)
        parser.add_argument("--vacation-closure", action="store_true", help="Cierre vacaciones")
        parser.add_argument("--reason", type=str, help="Motivo del cierre")
        parser.add_argument("--start", type=str, help="Fecha inicio cierre")
        parser.add_argument("--end", type=str, help="Fecha fin cierre")
        parser.add_argument("--reopen", type=str, help="Fecha reapertura")
        parser.add_argument("--tax-certificate", action="store_true", help="Certificado fiscal")
        parser.add_argument("--year", type=int, help="Año fiscal")
        parser.add_argument("--quarterly-receipt", action="store_true", help="Recibo trimestral")
        parser.add_argument("--student-id", type=int, help="ID del estudiante")
        parser.add_argument("--months", type=str, help="Meses del trimestre (mes1,mes2,mes3)")

    def handle(self, *args, **options):
        template_name = options["template"]

        if options["test"]:
            self.send_test_email(template_name, options)
        elif options["birthdays"]:
            self.send_birthday_emails()
        elif options["monthly_reports"]:
            self.send_monthly_reports()
        elif options["fun_friday"]:
            self.send_fun_friday_emails(options)
        elif options["payment_reminder"]:
            self.send_payment_reminder_emails(options)
        elif options["vacation_closure"]:
            self.send_vacation_closure_emails(options)
        elif options["tax_certificate"]:
            self.send_tax_certificate_email_cmd(options)
        elif options["quarterly_receipt"]:
            self.send_quarterly_receipt(options)
        elif options["recipient"]:
            self.send_single_email(template_name, options)
        else:
            raise CommandError("Se requiere --recipient (o usa --test para prueba)")

    def send_test_email(self, template_name, options):
        recipient = settings.EMAIL_HOST_USER
        subject = options.get("subject") or f"Test Email - {template_name}"
        context = {
            "name": "Usuario de Prueba",
            "student_name": "Estudiante Test",
            "amount": 100.50,
            "due_date": "31/12/2025",
        }
        if options.get("context"):
            try:
                context.update(json.loads(options["context"]))
            except json.JSONDecodeError:
                self.stdout.write(self.style.WARNING("Context JSON inválido, usando valores por defecto"))
        self.stdout.write(f"Enviando email de prueba a {recipient}...")
        success = email_service.send_email(
            template_name=template_name, recipients=recipient, subject=subject, context=context
        )
        self.stdout.write(self.style.SUCCESS("Email enviado") if success else self.style.ERROR("Error al enviar"))

    def send_single_email(self, template_name, options):
        recipient = options["recipient"]
        subject = options.get("subject") or "Five a Day"
        context = json.loads(options["context"]) if options.get("context") else {}
        success = email_service.send_email(
            template_name=template_name, recipients=recipient, subject=subject, context=context
        )
        self.stdout.write(self.style.SUCCESS("Email enviado") if success else self.style.ERROR("Error al enviar"))

    def send_birthday_emails(self):
        today = date.today()
        # The email filter goes INSIDE the prefetch: `parents.exclude(...).first()` on
        # the prefetched manager built a new queryset and re-queried per student,
        # making the `prefetch_related` above dead weight.
        birthday_students = list(
            Student.objects.filter(
                birth_date__month=today.month, birth_date__day=today.day, active=True, is_waiting=False
            ).prefetch_related(
                Prefetch(
                    "parents",
                    queryset=Parent.objects.exclude(email="").exclude(email__isnull=True),
                    to_attr="emailable_parents",
                )
            )
        )
        if not birthday_students:
            self.stdout.write(self.style.WARNING("No hay cumpleaños hoy"))
            return
        sent, failed = 0, 0
        for student in birthday_students:
            parent = next(iter(student.emailable_parents), None)
            if not parent:
                failed += 1
                continue
            success = email_service.send_email(
                template_name="happy_birthday",
                recipients=parent.email,
                subject=f"¡Feliz Cumpleaños {student.first_name}!",
                context={"name": student.first_name},
            )
            sent += 1 if success else 0
            failed += 0 if success else 1
        self.stdout.write(self.style.SUCCESS(f"Resultado: {sent} enviados, {failed} fallidos"))

    def send_monthly_reports(self):
        # Was three queries per parent: `children.filter(active=True)`, then `s.group`
        # for every child (no select_related), then `students.count()` re-running the
        # same queryset. All of it now rides on one prefetch.
        # Only parents of ENROLLED students, deduplicated by address. This used
        # to be every `Parent` row with an email — including families that have
        # left, and waiting-list families — each of whom received a report
        # listing zero students.
        seen: set[str] = set()
        parents = []
        for parent in (
            Parent.objects.filter(**_mass_mail_parent_filters())
            .exclude(email="")
            .exclude(email__isnull=True)
            .distinct()
            .order_by("id")
            .prefetch_related(
                Prefetch(
                    "children",
                    queryset=Student.objects.filter(active=True, is_waiting=False).select_related("group"),
                    to_attr="active_children",
                )
            )
        ):
            key = parent.email.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            parents.append(parent)
        if not parents:
            self.stdout.write(self.style.WARNING("No hay padres con email"))
            return
        sent, failed = 0, 0
        for parent in parents:
            students = parent.active_children
            context = {
                "parent_name": parent.full_name,
                "students": [
                    {"name": s.full_name, "group": s.group.group_name if s.group else "Sin grupo"} for s in students
                ],
                "total_students": len(students),
            }
            success = email_service.send_email(
                template_name="monthly_report",
                recipients=parent.email,
                subject="Reporte Mensual - Five a Day",
                context=context,
            )
            sent += 1 if success else 0
            failed += 0 if success else 1
        self.stdout.write(self.style.SUCCESS(f"Resultado: {sent} enviados, {failed} fallidos"))

    def send_fun_friday_emails(self, options):
        if not options.get("activity"):
            raise CommandError("Se requiere --activity")
        if not options.get("date"):
            raise CommandError("Se requiere --date (YYYY-MM-DD)")
        if not options.get("time"):
            raise CommandError("Se requiere --time (HH:MM-HH:MM)")
        try:
            event_date = datetime.strptime(options["date"], "%Y-%m-%d")
        except ValueError as err:
            raise CommandError("Formato de fecha inválido") from err
        start_time, end_time = options["time"].split("-")
        recipient_emails = _mass_mail_recipients()
        # Send one email per parent. Previously the whole list was passed as
        # `recipients=...` which put every family's address in the `To:` header
        # of a single message — GDPR breach + enumeration leak. Loop instead.
        sent = 0
        for email in recipient_emails:
            if send_fun_friday_email(
                recipients=email,
                day_name=DIAS_ES[event_date.weekday()],
                day_number=event_date.day,
                month=MESES_ES[event_date.month - 1],
                start_time=start_time,
                end_time=end_time,
                activity_description=options["activity"],
                minimum_age=options.get("min_age", 4),
                maximum_age=options.get("max_age", 12),
                meeting_point=options.get("meeting_point"),
                event_image_path=options.get("event_image"),
            ):
                sent += 1
        self.stdout.write(self.style.SUCCESS(f"Enviado a {sent}/{len(recipient_emails)} padres"))

    def send_payment_reminder_emails(self, options):
        if not options.get("month"):
            raise CommandError("Se requiere --month")

        # Resolve the fee/IBAN from the real sources when the flags are omitted,
        # so this command bills the same numbers the app does — SiteConfiguration
        # is the single source of truth for prices.
        import os

        from billing.models import SiteConfiguration

        config = SiteConfiguration.get_config()
        iban = options.get("iban") or os.getenv("ACADEMY_IBAN", "")
        # ACADEMY_PHONE, matching app_forms.py — production stores the Bizum
        # number there (= the old hard-coded 613 481 141), and no
        # ACADEMY_BIZUM_PHONE var exists on the service.
        bizum_phone = options.get("bizum_phone") or os.getenv("ACADEMY_PHONE", "")
        # `emails/payment_reminder.html` prints "{{ reduced_price_cheque_idioma }}
        # euros", so the "€" this used to append rendered as "34€ euros". One
        # helper formats it now (the same one the app's four call sites use), and
        # a "€" typed after `--cheque-idioma-price` is stripped rather than
        # doubled up.
        cheque_price = (options.get("cheque_idioma_price") or "").strip().removesuffix("€").strip()
        if not cheque_price:
            cheque_price = cheque_idioma_fee(config)

        recipient_emails = _mass_mail_recipients()
        # Per-parent loop — see send_fun_friday_emails for the "batch in To:"
        # bug this replaces.
        sent = 0
        for email in recipient_emails:
            if send_payment_reminder_email(
                recipients=email,
                payment_start_day_name=DIAS_ES[0],
                payment_start_day_number=int(options.get("payment_start", 1)),
                payment_end_day_name=DIAS_ES[4],
                payment_end_day_number=int(options.get("payment_end", 5)),
                month=options["month"],
                iban_number=iban,
                reduced_price_cheque_idioma=cheque_price,
                telephone_number_bizum=bizum_phone,
            ):
                sent += 1
        self.stdout.write(self.style.SUCCESS(f"Enviado a {sent}/{len(recipient_emails)} padres"))

    def send_vacation_closure_emails(self, options):
        for field in ("reason", "start", "end", "reopen"):
            if not options.get(field):
                raise CommandError(f"Se requiere --{field}")
        start_date = datetime.strptime(options["start"], "%Y-%m-%d")
        end_date = datetime.strptime(options["end"], "%Y-%m-%d")
        reopen_date = datetime.strptime(options["reopen"], "%Y-%m-%d")
        recipient_emails = _mass_mail_recipients()
        # Per-parent loop — see send_fun_friday_emails for the "batch in To:"
        # bug this replaces.
        sent = 0
        for email in recipient_emails:
            if send_vacation_closure_email(
                recipients=email,
                start_closure_day_name=DIAS_ES[start_date.weekday()],
                start_closure_day_number=start_date.day,
                end_closure_day_name=DIAS_ES[end_date.weekday()],
                end_closure_day_number=end_date.day,
                month_closure=MESES_ES[start_date.month - 1],
                # The end month can differ from the start month (Christmas
                # closure = Dec 23 → Jan 3). Pass both so the template can
                # render "23 de diciembre" and "3 de enero" correctly instead
                # of collapsing to a single month.
                month_closure_end=MESES_ES[end_date.month - 1],
                closure_reason=options["reason"],
                reopening_day_name=DIAS_ES[reopen_date.weekday()],
                reopening_day_number=reopen_date.day,
                month_reopening=MESES_ES[reopen_date.month - 1],
            ):
                sent += 1
        self.stdout.write(self.style.SUCCESS(f"Enviado a {sent}/{len(recipient_emails)} padres"))

    def send_tax_certificate_email_cmd(self, options):
        if not options.get("year"):
            raise CommandError("Se requiere --year")
        year = options["year"]
        if options.get("recipient"):
            # iexact + explicit ambiguity handling: Parent.email is not unique
            # and Postgres compares it case-sensitively, so a bare
            # `.get(email=...)` both missed a differently-cased address and
            # raised an opaque MultipleObjectsReturned on a shared mailbox.
            # `[:2]` + refuse-if-not-exactly-one is the same call
            # `core.views.auth._parent_by_email` makes, and for the same reason:
            # picking whichever row came back first would mail one family's
            # fiscal certificate — with the other family's payments and DNI on
            # it — to the shared address. The local copy is deliberate; a comms
            # command importing `core.views` would invert the layering.
            #
            # There is no `except Parent.DoesNotExist` here any more: nothing in
            # this block can raise it (the `.get()` it guarded was replaced by
            # the slice above), so it was dead code that read like a live path.
            recipient = options["recipient"]
            matches = list(Parent.objects.filter(email__iexact=recipient)[:2])
            if not matches:
                raise CommandError(f"No se encontró padre con email {recipient}")
            if len(matches) > 1:
                raise CommandError(f"Hay varios padres con el email {recipient}; resuélvelo antes de enviar.")
            parent = matches[0]
            success = send_tax_certificate_email(parent=parent, year=year)
            self.stdout.write(
                self.style.SUCCESS(f"Certificado enviado a {parent.email}") if success else self.style.ERROR("Error")
            )
        else:
            results = send_all_tax_certificates(year)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Resultado: {results['sent']} enviados, {results['skipped']} omitidos, {results['failed']} fallidos"
                )
            )

    def send_quarterly_receipt(self, options):
        if not options.get("student_id"):
            raise CommandError("Se requiere --student-id")
        if not options.get("months"):
            raise CommandError("Se requiere --months (mes1,mes2,mes3)")
        student = Student.objects.get(pk=options["student_id"])
        parent = student.parents.exclude(email="").exclude(email__isnull=True).order_by("id").first()
        if not parent:
            raise CommandError(f"{student.full_name} no tiene padre con email")
        months = options["months"].split(",")
        if len(months) != 3:
            raise CommandError("Se requieren exactamente 3 meses")
        success = send_quarterly_receipt_email(
            parent_email=parent.email,
            student_name=student.full_name,
            month_1=months[0].strip(),
            month_2=months[1].strip(),
            month_3=months[2].strip(),
        )
        self.stdout.write(self.style.SUCCESS("Recibo enviado") if success else self.style.ERROR("Error"))
