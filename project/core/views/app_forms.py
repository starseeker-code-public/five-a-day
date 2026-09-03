"""
Email app form views — each view handles GET (show form with email preview)
and POST (send emails to parents).

Roughly 400 of this module's lines used to be duplication: 9 near-verbatim
preview/test-send blocks, 6 copies of the same send loop (each carrying an
identical 6-line comment) and 5 tally tails. They are now two shared helpers,
`_preview_or_test` and `_mass_send`, plus one shared definition of "who a mass
mail reaches" (`_recipient_filters` / `_parent_recipients` / `_mass_mail_parents`).
That is not tidying for its own sake — every one of the bugs below existed in
some copies and not others, which is exactly what copy-paste guarantees:

* waiting-list families received every mass mail (see `_recipient_filters`);
* the Fun Friday recipient COUNT, the sent set and the success message were
  three different numbers;
* no send deduplicated addresses, so a couple sharing a mailbox got two copies;
* opening the SMTP connection could 500 the request (`_mass_send`);
* the preview and the send resolved a student's guardian by different query
  paths (`_emailable_parent`).
"""

import logging
import os
from datetime import date, time, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

from billing.services.pricing_service import PricingService
from comms.services.email_functions import (
    cheque_idioma_fee,
    send_all_tax_certificates,
    send_monthly_report,
    send_payment_reminder_email,
    send_quarterly_receipt_email,
    send_vacation_closure_email,
    send_welcome_email,
)
from comms.services.email_service import email_service
from core.constants import DIAS_ES, MESES_ES
from core.decorators import admin_required
from core.models import HistoryLog
from core.utils import MAX_QUERY_YEAR, MIN_QUERY_YEAR, safe_int
from students.models import Group, Parent, Student

logger = logging.getLogger(__name__)


def _birthday_image_path():
    """Absolute path to the inline birthday card image.

    The template's `<img src="cid:birthday_image">` renders BROKEN without a
    matching `inline_images` attachment — the cron passes it, but the manual
    test-send and mass-send here did not, so the admin's own test could never
    reproduce what parents complained about. Same file the cron and
    test_all_emails use.
    """
    from django.conf import settings

    return os.path.join(settings.BASE_DIR, "core/static/images/happy-birthday.png")


#: Parents of a Student who actually have an email address, exposed as the plain
#: list `student.emailable_parents`.
#:
#: The birthday loops did `student.parents.exclude(email="").exclude(email__isnull=True)
#: .first()` per student — an unprefetched related-manager query on every iteration.
#: `order_by("id")` is not cosmetic: the old `.first()` sorted by pk (an unordered
#: queryset makes `first()` add `order_by("pk")`), so dropping the ordering would
#: quietly change WHICH parent receives the email when a student has two.
_EMAILABLE_PARENTS_PREFETCH = Prefetch(
    "parents",
    queryset=Parent.objects.exclude(email="").exclude(email__isnull=True).order_by("id"),
    to_attr="emailable_parents",
)


#: Active children of a Parent, resolved in ONE extra query for the whole queryset
#: and exposed as the plain list `parent.active_children`.
#:
#: Three mass-mail paths (monthly report, quarterly receipts, enrollment receipts)
#: used `prefetch_related("children")` and then `parent.children.filter(active=True)`
#: inside the loop. A `.filter()` on a related manager builds a NEW queryset, so the
#: prefetch cache is discarded and every parent costs a round trip — measured at 365
#: queries for 120 parents, against 2 with this Prefetch. `select_related("group")`
#: is folded in because the monthly report reads each child's group name.
#:
#: `is_waiting=False` matches `_recipient_filters`: without it a family whose child
#: was moved back onto the waiting list still had that child listed in their monthly
#: report and still got a receipt for them.
_ACTIVE_CHILDREN_PREFETCH = Prefetch(
    "children",
    queryset=Student.objects.filter(active=True, is_waiting=False)
    .select_related("group")
    .order_by("first_name", "last_name"),
    to_attr="active_children",
)


def _safe_year(raw, default: int) -> int:
    """Parse a year from form input, falling back to `default`.

    A bare `int(request.POST.get("year"))` here was a 500 on any non-numeric
    input; the range keeps it inside what a DateField can hold. Delegates to
    `core.utils.safe_int` so the app has one implementation of this rather than
    the three it had grown.
    """
    return safe_int(raw, default=default, low=MIN_QUERY_YEAR, high=MAX_QUERY_YEAR)


# ============================================================================
# WHO A MASS MAIL REACHES — one definition, used by both the count and the send
# ============================================================================


def _recipient_filters(*, group=None, include_adult_students: bool = True) -> dict:
    """The filters EVERY mass-mail recipient query must carry.

    `children__is_waiting=False` is the one that was missing everywhere. A
    waiting-list entry taken over the phone has no `Parent` row at all (its
    contact lives on `Student.waiting_contact_name` / `waiting_contact_phone`),
    so it looked harmless — but `add_to_waiting_list` moves an ENROLLED student
    onto the list by cancelling the enrollment and leaving `active=True`, and
    that student keeps their parents. Filtering on `active` alone therefore sent
    payment reminders, receipts and every announcement to families whose child
    is not enrolled and owes nothing.

    `include_adult_students=False` drops parents whose only active child is an
    adult student — Fun Friday is a children's activity with a min/max age, and
    its counter already excluded them while its send did not.
    """
    filters = {"children__active": True, "children__is_waiting": False}
    if not include_adult_students:
        filters["children__is_adult"] = False
    if group is not None:
        filters["children__group"] = group
    return filters


def _dedupe_emails(addresses) -> list[str]:
    """Collapse addresses that differ only by case or surrounding whitespace.

    `Parent.email` is NOT unique — a couple sharing one mailbox is two
    legitimate rows (see CLAUDE.md) — so every mass send delivered one copy per
    ROW, and a family with both parents on file got everything twice. The
    comparison is case-insensitive because SMTP local-parts are compared
    case-insensitively in practice and `Maria@x` / `maria@x` is one inbox.
    Insertion order is preserved so the lowest-id parent's spelling wins and the
    send order stays deterministic.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in addresses:
        address = (raw or "").strip()
        if not address:
            continue
        key = address.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(address)
    return ordered


def _parent_recipients(*, group=None, include_adult_students: bool = True) -> list[str]:
    """The addresses a mass mail actually reaches — deduplicated.

    THE counted set and THE sent set: every form renders `parent_count` from
    `len()` of this list and then sends to the same list, so the number on the
    page, the number of messages and the number in the success banner can no
    longer disagree. The six `parent_count` figures also all lacked the
    `if p.email` filter, so they overstated reachable recipients by however many
    families have no address on file.
    """
    rows = (
        Parent.objects.filter(**_recipient_filters(group=group, include_adult_students=include_adult_students))
        .exclude(email="")
        .exclude(email__isnull=True)
        .order_by("id")
        .values_list("email", flat=True)
    )
    return _dedupe_emails(rows)


def _mass_mail_parents(*, group=None, include_adult_students: bool = True, with_children: bool = False) -> list:
    """`Parent` rows for a PERSONALISED mass mail — one row per distinct address.

    Same population as `_parent_recipients`, but the objects, for the sends that
    need the parent's name or their children (monthly report, receipts).
    `with_children` attaches `_ACTIVE_CHILDREN_PREFETCH` so the loop reads a
    plain list instead of re-querying per parent.

    Deduplication is by address, not by row: two `Parent` rows sharing a mailbox
    are the same couple with the same children, so one message is right and two
    were the bug. If two unrelated families genuinely shared an address the
    second would be skipped — that is a data error worth surfacing, and one
    message is still the safer direction.
    """
    qs = (
        Parent.objects.filter(**_recipient_filters(group=group, include_adult_students=include_adult_students))
        .exclude(email="")
        .exclude(email__isnull=True)
        .distinct()
        .order_by("id")
    )
    if with_children:
        qs = qs.prefetch_related(_ACTIVE_CHILDREN_PREFETCH)

    seen: set[str] = set()
    parents = []
    for parent in qs:
        key = parent.email.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        parents.append(parent)
    return parents


def _emailable_parent(student):
    """The guardian a single-student email goes to — ONE resolution.

    `enrollment_form`'s preview did `parents.exclude(...).first()` while its send
    did `parents.exclude(...).order_by("id").first()`. Both happen to sort by pk
    today (an unordered queryset makes `.first()` add `order_by("pk")`, and no
    model in `students.models` sets `Meta.ordering`), so the preview could show a
    different guardian than the one who receives the mail the moment either side
    grew an ordering. One helper, explicit ordering — the same pick the payment
    generators make, for the same reason.

    Honours `_EMAILABLE_PARENTS_PREFETCH` when the caller used it: `.filter()` /
    `.first()` on a prefetched related manager builds a NEW queryset and throws
    the prefetch away (CLAUDE.md), which is how the mass-mail views measured 365
    queries for 120 parents.
    """
    cached = getattr(student, "emailable_parents", None)
    if cached is not None:
        return next(iter(cached), None)
    return student.parents.exclude(email="").exclude(email__isnull=True).order_by("id").first()


# ============================================================================
# PREVIEW / TEST SEND — one implementation for all 9 forms
# ============================================================================


def _preview_or_test(action: str, template_name: str, context: dict, subject: str, *, inline_images=None):
    """Handle the `action=preview` / `action=test_send` branch of a mail form.

    Nine views carried a byte-for-byte copy of this: render for `preview`, read
    `EMAIL_TEST_1`/`EMAIL_TEST_2` for `test_send`, and return one of three fixed
    JSON shapes. The only real behaviour change is that a raising `send_email`
    (SMTP down) is now reported as "❌ Error al enviar el email de prueba"
    instead of 500ing the AJAX call.
    """
    if action == "preview":
        return JsonResponse({"html": render_to_string(f"emails/{template_name}.html", context)})

    recipients = [r for r in (os.getenv("EMAIL_TEST_1", ""), os.getenv("EMAIL_TEST_2", "")) if r]
    if not recipients:
        return JsonResponse({"success": False, "message": "❌ EMAIL_TEST_1/EMAIL_TEST_2 no configurados"})

    try:
        sent = email_service.send_email(
            template_name=template_name,
            recipients=recipients,
            subject=subject,
            context=context,
            inline_images=inline_images,
        )
    except Exception:
        # The test-send button exists to diagnose the mail path, so it must
        # report a dead SMTP hop rather than crash on it. The recipients are the
        # operator's own test addresses and are not logged.
        logger.exception("Test send failed for template '%s'", template_name)
        sent = False

    if sent:
        return JsonResponse({"success": True, "message": f"✅ Email de prueba enviado a {', '.join(recipients)}"})
    return JsonResponse({"success": False, "message": "❌ Error al enviar el email de prueba"})


# ============================================================================
# MASS SEND — one implementation for all 6 batch forms
# ============================================================================


def _mass_send(request, jobs: list[dict], sender, *, log_label: str, success_text: str, failure_text: str):
    """Deliver one batch over ONE SMTP session and report accurately.

    `jobs` is a list of kwarg dicts — one message each — and `sender` is called
    as ``sender(connection=<conn>, **job)``, returning truthy on success.
    Returns ``(sent, failed)``. `success_text` / `failure_text` are Spanish
    templates carrying the literal `{count}`, substituted with `str.replace` and
    NOT `str.format`: the newsletter banner interpolates a group name, and a
    group called `A{B}` would make `.format()` raise *after* the batch had gone
    out.

    Six views had a near-verbatim copy of this loop, each with its own copy of
    the same six-line comment and its own `success_count` / `error_count` tail.
    Four things are fixed here once instead of six times:

    * **ONE SMTP session per batch.** Every loop except `birthday_form`'s opened
      a fresh TCP+TLS+AUTH per recipient; at roster scale that handshake IS the
      request. The senders take a `connection=` for exactly this.
    * **Opening the connection cannot 500.** `with email_service.open_connection()`
      calls `open()` with `fail_silently=False`, so a TCP/TLS/AUTH failure
      propagated out of `birthday_form` as a 500 where the older per-student loop
      had reported "⚠️ N email(s) no pudieron enviarse". The open is wrapped, the
      operator gets a Spanish error, and every message counts as unsent.
    * **A per-message failure never aborts the batch** — the remaining families
      still get their mail, which is the whole point of a mass send.
    * **The operator always gets a record.** `HistoryLog` was written only when
      `success_count > 0`, so a total mail outage left NOTHING in the activity
      feed — the one case where the record matters most. One entry is written
      whenever there was anything to send, carrying both figures.

    NOT taken further into a Celery task: production runs
    `CELERY_TASK_ALWAYS_EAGER=True` with no worker, so a task would execute
    inline in this same request and buy nothing, while making the report the
    operator reads unreliable. The structural fix is the persisted-row + drain
    pattern (`FunFridayScheduledSend`), which `fun_friday_form` already uses.
    """
    if not jobs:
        return 0, 0

    view_name = request.resolver_match.url_name if request.resolver_match else "app_forms"

    try:
        connection = email_service.open_connection()
        connection.open()
    except Exception:
        logger.exception("SMTP connection could not be opened in %s", view_name)
        messages.error(
            request,
            f"❌ No se pudo conectar con el servidor de correo. No se ha enviado ningún email ({len(jobs)} pendientes).",
        )
        HistoryLog.log(
            "email_sent",
            f"{log_label}: 0 enviados, {len(jobs)} fallidos (sin conexión con el servidor de correo)",
            icon="mail",
        )
        return 0, len(jobs)

    success_count = 0
    error_count = 0
    try:
        for job in jobs:
            try:
                if sender(connection=connection, **job):
                    success_count += 1
                else:
                    error_count += 1
            except Exception:
                # Never silent: this is the academy's outbound channel, and an
                # operator who sees only a count cannot tell a bad address from
                # an SMTP outage. The recipient is NOT logged — the exception
                # text can carry it, so `logger.exception` alone is the record.
                logger.exception("Email send failed in %s", view_name)
                error_count += 1
    finally:
        try:
            connection.close()
        except Exception:
            logger.exception("SMTP connection could not be closed in %s", view_name)

    detail = f"{success_count} email(s) enviados"
    if error_count:
        detail = f"{detail}, {error_count} fallidos"
    HistoryLog.log("email_sent", f"{log_label}: {detail}", icon="mail")

    if success_count:
        messages.success(request, success_text.replace("{count}", str(success_count)))
    if error_count:
        messages.warning(request, failure_text.replace("{count}", str(error_count)))
    return success_count, error_count


def _hhmm(raw) -> str | None:
    """Normalise a time input to `HH:MM`, or None when it is not a time.

    `<input type="time">` submits seconds ("17:30:00") in several browsers and
    locales, and `FunFridayScheduledSend.start_time` is a `CharField(max_length=5)`
    — so the raw value reached Postgres as 8 characters, raised a `DataError`,
    and the announcement the teacher had just composed was lost with the 500.
    """
    try:
        return time.fromisoformat(str(raw).strip()).strftime("%H:%M")
    except (AttributeError, TypeError, ValueError):
        return None


def _cheque_idioma_text(raw, config) -> str:
    """The cheque-idioma figure exactly as the template prints it.

    `emails/payment_reminder.html` renders `{{ reduced_price_cheque_idioma }}
    euros`, so a value carrying its own "€" produced "34€ euros" — and every
    caller added the symbol, so production has sent the double unit since v1.0.
    The default now comes from `cheque_idioma_fee` (SiteConfiguration; the POST
    path still had a hard-coded "34€" while GET and preview derived it), and a
    trailing "€" typed into the form by an operator is stripped rather than
    rendered.
    """
    text = (raw or "").strip().removesuffix("€").strip()
    return text or cheque_idioma_fee(config)


@admin_required
def apps_view(request):
    """Vista para la página de aplicaciones/herramientas"""
    return render(request, "apps.html")


# ============================================================================
# FUN FRIDAY - Formulario de envío masivo
# ============================================================================


@admin_required
def fun_friday_form(request):
    """
    Vista para el formulario de Fun Friday.
    GET: Muestra el formulario con valores por defecto
    POST: Valida HTML y programa emails a todos los padres con estudiantes activos
    """
    today = date.today()
    days_until_friday = (4 - today.weekday()) % 7
    if days_until_friday == 0:
        days_until_friday = 7
    next_friday = today + timedelta(days=days_until_friday)

    # ONE resolution for the counter, the scheduled recipients and the success
    # message. These were three different numbers: the counter excluded adult
    # students and parents without an address, the send excluded neither, and
    # the banner reported the length of a third list.
    parent_emails = _parent_recipients(include_adult_students=False)
    parent_count = len(parent_emails)

    default_html = """<strong>🎉 ¡SESIÓN DE MANUALIDADES!</strong>
<br><br>
Esta semana haremos manualidades creativas con materiales reciclados.
<br><br>
<em>Los niños deben traer:</em>
<ul>
    <li>Una camiseta vieja</li>
    <li>Tijeras de punta redonda</li>
</ul>
<br>
¡Os esperamos! 🎨"""

    def _reshow(text):
        """Re-render the form KEEPING what the teacher wrote.

        Every error path here has to preserve `activity_description`: it is a
        composed announcement, sometimes several minutes of typing, and losing
        it to a validation error is the difference between a warning and a
        support call.
        """
        return render(
            request,
            "apps/fun_friday_form.html",
            {
                "next_friday": next_friday.isoformat(),
                "parent_count": parent_count,
                "default_html": text or default_html,
            },
        )

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in ("preview", "test_send"):
            _event_date_str = request.POST.get("event_date", next_friday.isoformat())
            _meeting_point = request.POST.get("meeting_point", "")
            _activity = request.POST.get("activity_description", default_html)
            _start_time = _hhmm(request.POST.get("start_time")) or "17:30"
            _end_time = _hhmm(request.POST.get("end_time")) or "18:30"
            try:
                _ed = date.fromisoformat(_event_date_str)
            except (ValueError, TypeError):
                _ed = next_friday
            try:
                _min_age = int(request.POST.get("min_age", 5))
                _max_age = int(request.POST.get("max_age", 12))
            except (ValueError, TypeError):
                _min_age, _max_age = 5, 12
            _ctx = {
                "day_name": DIAS_ES[_ed.weekday()],
                "day_number": _ed.day,
                "month": MESES_ES[_ed.month - 1],
                "start_time": _start_time,
                "end_time": _end_time,
                "activity_description": _activity,
                "meeting_point": _meeting_point,
                "minimum_age": _min_age,
                "maximum_age": _max_age,
            }
            return _preview_or_test(
                action,
                "fun_friday",
                _ctx,
                f"[TEST] 🎉 Fun Friday - {_ctx['day_name'].capitalize()} {_ctx['day_number']} de {_ctx['month']}",
            )

        # Obtener datos del formulario
        event_date_str = request.POST.get("event_date")
        raw_start_time = request.POST.get("start_time")
        raw_end_time = request.POST.get("end_time")
        meeting_point = request.POST.get("meeting_point", "")
        min_age = request.POST.get("min_age")
        max_age = request.POST.get("max_age")
        activity_description = request.POST.get("activity_description", "")

        if not all([event_date_str, raw_start_time, raw_end_time, min_age, max_age, activity_description]):
            messages.error(request, "❌ Todos los campos obligatorios son requeridos")
            return _reshow(activity_description)

        try:
            event_date = date.fromisoformat(event_date_str)
        except ValueError:
            messages.error(request, "❌ Fecha inválida")
            return _reshow(activity_description)

        start_time = _hhmm(raw_start_time)
        end_time = _hhmm(raw_end_time)
        if not start_time or not end_time:
            messages.error(request, "❌ Hora inválida. Usa el formato HH:MM (por ejemplo 17:30).")
            return _reshow(activity_description)

        try:
            min_age_int = int(min_age)
            max_age_int = int(max_age)
            if min_age_int > max_age_int:
                raise ValueError
        except ValueError:
            messages.error(request, "❌ Las edades deben ser números y la mínima no puede superar la máxima.")
            return _reshow(activity_description)

        day_name = DIAS_ES[event_date.weekday()]
        month_name = MESES_ES[event_date.month - 1]

        if not parent_emails:
            messages.warning(request, "⚠️ No hay padres con email para enviar")
            return redirect("home")

        # Don't send now — persist the announcement scheduled for 14:30 on the
        # MONDAY of the target Friday's week (e.g. a Friday on the 17th →
        # Monday the 13th at 14:30). A DB row (not apply_async(eta=...)) so it
        # survives eager mode: send_due_fun_friday_emails_task drains due rows
        # (Celery Beat in dev/testing, Cloud Scheduler job in production). If
        # the moment has already passed, drain immediately.
        import datetime as _dt

        from comms.tasks import _dispatch, send_due_fun_friday_emails_task
        from core.models import FunFridayScheduledSend

        monday = event_date - timedelta(days=event_date.weekday())
        send_at = _dt.datetime.combine(monday, _dt.time(14, 30))
        if timezone.is_naive(send_at):
            send_at = timezone.make_aware(send_at, timezone.get_current_timezone())

        # A double submit used to create a SECOND scheduled row for the same
        # announcement — and when the slot has already passed the row is drained
        # on creation, so the second submit mailed every family twice. Matched on
        # the announcement's natural key (slot + day + month + text) regardless of
        # `sent_at`, because "already sent" is precisely the case that must not
        # be repeated. Changing the text is what makes it a new announcement.
        already_scheduled = FunFridayScheduledSend.objects.filter(
            scheduled_for=send_at,
            day_number=event_date.day,
            month=month_name,
            activity_description=activity_description,
        ).exists()
        if already_scheduled:
            messages.info(
                request,
                f"ℹ️ Este anuncio de Fun Friday ya estaba programado para el "
                f"{monday.strftime('%d/%m')} a las 14:30. No se ha duplicado.",
            )
            return redirect("home")

        scheduled = FunFridayScheduledSend(
            recipients=parent_emails,
            day_name=day_name,
            day_number=event_date.day,
            month=month_name,
            start_time=start_time,
            end_time=end_time,
            activity_description=activity_description,
            minimum_age=min_age_int,
            maximum_age=max_age_int,
            meeting_point=meeting_point if meeting_point else None,
            scheduled_for=send_at,
        )
        try:
            # `objects.create()` does NOT validate (CLAUDE.md), and every field
            # here comes straight from raw POST. Without this a value the column
            # cannot hold reached Postgres as a `DataError` — a 500 that also
            # threw away the composed announcement.
            scheduled.full_clean()
        except ValidationError:
            logger.exception("FunFridayScheduledSend validation failed in fun_friday_form")
            messages.error(
                request,
                "❌ Los datos del anuncio no son válidos. Revisa las horas (HH:MM), las edades y el punto de encuentro.",
            )
            return _reshow(activity_description)
        scheduled.save()

        if send_at <= timezone.now():
            # `.delay()` was the only non-fail-soft dispatch in the tree: under
            # production's eager+propagate settings a drain failure raised INTO
            # this POST, 500ing it after the row had already committed — so the
            # teacher saw a crash for an announcement that was safely stored.
            # `_dispatch` logs and returns False instead. `on_commit` because the
            # drain reads the row back by id, so it must be committed first
            # (a no-op outside an atomic block, which is where this normally runs).
            transaction.on_commit(lambda: _dispatch(send_due_fun_friday_emails_task, what="Fun Friday drain"))

        HistoryLog.log(
            "email_scheduled",
            f"Fun Friday ({day_name} {event_date.day} {month_name}): "
            f"{len(parent_emails)} email(s) programados para el {monday.strftime('%d/%m')} a las 14:30",
            icon="schedule_send",
        )
        messages.success(
            request,
            f"✅ Fun Friday programado: {len(parent_emails)} email(s) se enviarán "
            f"el lunes {monday.strftime('%d/%m')} a las 14:30.",
        )
        return redirect("home")

    # GET - Mostrar formulario con email preview
    email_html = render_to_string(
        "emails/fun_friday.html",
        {
            "day_name": DIAS_ES[next_friday.weekday()],
            "day_number": next_friday.day,
            "month": MESES_ES[next_friday.month - 1],
            "start_time": "17:30",
            "end_time": "18:30",
            "activity_description": default_html,
            "meeting_point": "En la puerta principal del centro",
            "minimum_age": 5,
            "maximum_age": 12,
        },
    )
    return render(
        request,
        "apps/fun_friday_form.html",
        {
            "next_friday": next_friday.isoformat(),
            "parent_count": parent_count,
            "default_html": default_html,
            "email_html": email_html,
        },
    )


# ============================================================================
# RECORDATORIO DE PAGO - Formulario de envío
# ============================================================================


@admin_required
def payment_reminder_form(request):
    """
    Vista para enviar recordatorios de pago mensual/trimestral.
    GET: Muestra formulario con valores por defecto
    POST: Envía recordatorio a todos los padres con estudiantes activos
    """
    from billing.models import SiteConfiguration

    today = date.today()
    config = SiteConfiguration.get_config()
    parent_emails = _parent_recipients()
    parent_count = len(parent_emails)

    default_start = today.replace(day=1)
    try:
        default_end = today.replace(day=5)
    except ValueError:
        default_end = today.replace(day=28)

    current_month = MESES_ES[today.month - 1]
    cheque_price = cheque_idioma_fee(config)

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in ("preview", "test_send"):
            _start_str = request.POST.get("payment_start_date", default_start.isoformat())
            _end_str = request.POST.get("payment_end_date", default_end.isoformat())
            _month = request.POST.get("month", current_month)
            _iban = request.POST.get("iban_number", os.getenv("ACADEMY_IBAN", ""))
            _iban_holder = request.POST.get("iban_holder", os.getenv("ACADEMY_IBAN_HOLDER", ""))
            _bizum = request.POST.get("telephone_number_bizum", os.getenv("ACADEMY_PHONE", ""))
            _cheque = _cheque_idioma_text(request.POST.get("reduced_price_cheque_idioma"), config)
            try:
                _sd = date.fromisoformat(_start_str)
                _ed = date.fromisoformat(_end_str)
            except (ValueError, TypeError):
                _sd, _ed = default_start, default_end
            _ctx = {
                "payment_start_day_name": DIAS_ES[_sd.weekday()],
                "payment_start_day_number": _sd.day,
                "payment_end_day_name": DIAS_ES[_ed.weekday()],
                "payment_end_day_number": _ed.day,
                "month": _month,
                "iban_number": _iban,
                "iban_holder": _iban_holder,
                "reduced_price_cheque_idioma": _cheque,
                "telephone_number_bizum": _bizum,
                **PricingService.payment_reminder_fees(config),
            }
            return _preview_or_test(
                action, "payment_reminder", _ctx, f"[TEST] 💰 Recordatorio de Pago - {_month.title()}"
            )

        payment_start_date_str = request.POST.get("payment_start_date")
        payment_end_date_str = request.POST.get("payment_end_date")
        month = request.POST.get("month", current_month)
        iban_number = request.POST.get("iban_number", "")
        iban_holder = request.POST.get("iban_holder", os.getenv("ACADEMY_IBAN_HOLDER", ""))
        telephone_number_bizum = request.POST.get("telephone_number_bizum", "")
        reduced_price_cheque_idioma = _cheque_idioma_text(request.POST.get("reduced_price_cheque_idioma"), config)

        if not all([payment_start_date_str, payment_end_date_str, iban_number, telephone_number_bizum]):
            messages.error(request, "❌ Todos los campos obligatorios son requeridos")
        else:
            try:
                start_date = date.fromisoformat(payment_start_date_str)
                end_date = date.fromisoformat(payment_end_date_str)
            except ValueError:
                messages.error(request, "❌ Fecha inválida")
                return redirect("payment_reminder_form")

            if not parent_emails:
                messages.warning(request, "⚠️ No hay padres con email para enviar")
                return redirect("apps")

            fees = PricingService.payment_reminder_fees(config)
            jobs = [
                {
                    "recipients": email_addr,
                    "payment_start_day_name": DIAS_ES[start_date.weekday()],
                    "payment_start_day_number": start_date.day,
                    "payment_end_day_name": DIAS_ES[end_date.weekday()],
                    "payment_end_day_number": end_date.day,
                    "month": month,
                    "iban_number": iban_number,
                    "iban_holder": iban_holder,
                    "reduced_price_cheque_idioma": reduced_price_cheque_idioma,
                    "telephone_number_bizum": telephone_number_bizum,
                    **fees,
                }
                for email_addr in parent_emails
            ]
            _mass_send(
                request,
                jobs,
                send_payment_reminder_email,
                log_label="Recordatorio de pago",
                success_text="✅ Recordatorio enviado a {count} padre(s)",
                failure_text="⚠️ {count} email(s) no pudieron enviarse",
            )
            return redirect("apps")

    default_iban = os.getenv("ACADEMY_IBAN", "")
    default_bizum = os.getenv("ACADEMY_PHONE", "")

    email_html = render_to_string(
        "emails/payment_reminder.html",
        {
            "payment_start_day_name": DIAS_ES[default_start.weekday()],
            "payment_start_day_number": default_start.day,
            "payment_end_day_name": DIAS_ES[default_end.weekday()],
            "payment_end_day_number": default_end.day,
            "month": current_month,
            "iban_number": default_iban,
            "iban_holder": os.getenv("ACADEMY_IBAN_HOLDER", ""),
            "reduced_price_cheque_idioma": cheque_price,
            "telephone_number_bizum": default_bizum,
            **PricingService.payment_reminder_fees(config),
        },
    )
    return render(
        request,
        "apps/payment_reminder_form.html",
        {
            "parent_count": parent_count,
            "default_start_date": default_start.isoformat(),
            "default_end_date": default_end.isoformat(),
            "months": MESES_ES,
            "current_month": current_month,
            "default_iban": default_iban,
            "default_bizum": default_bizum,
            "default_cheque_price": cheque_price,
            "email_html": email_html,
        },
    )


# ============================================================================
# CIERRE POR VACACIONES - Formulario de envío
# ============================================================================


@admin_required
def vacation_closure_form(request):
    """
    Vista para enviar avisos de cierre por vacaciones.
    GET: Muestra formulario
    POST: Envía aviso a todos los padres con estudiantes activos
    """
    parent_emails = _parent_recipients()
    parent_count = len(parent_emails)

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in ("preview", "test_send"):
            _cs_str = request.POST.get("closure_start_date", "")
            _ce_str = request.POST.get("closure_end_date", "")
            _r_str = request.POST.get("reopening_date", "")
            _reason = request.POST.get("closure_reason", "Vacaciones")
            try:
                _cs = date.fromisoformat(_cs_str)
                _ce = date.fromisoformat(_ce_str)
                _ro = date.fromisoformat(_r_str)
            except (ValueError, TypeError):
                _cs = date.today()
                _ce = _cs + timedelta(days=7)
                _ro = _ce + timedelta(days=3)
            _ctx = {
                "start_closure_day_name": DIAS_ES[_cs.weekday()],
                "start_closure_day_number": _cs.day,
                "end_closure_day_name": DIAS_ES[_ce.weekday()],
                "end_closure_day_number": _ce.day,
                "month_closure": MESES_ES[_cs.month - 1],
                # end date's month may differ (e.g. Navidad 23 dic → 3 ene)
                "month_closure_end": MESES_ES[_ce.month - 1],
                "closure_reason": _reason,
                "reopening_day_name": DIAS_ES[_ro.weekday()],
                "reopening_day_number": _ro.day,
                "month_reopening": MESES_ES[_ro.month - 1],
            }
            return _preview_or_test(action, "vacation_closure", _ctx, f"[TEST] 🏖️ Cierre por {_reason} - Five a Day")

        closure_start_str = request.POST.get("closure_start_date")
        closure_end_str = request.POST.get("closure_end_date")
        closure_reason = request.POST.get("closure_reason", "")
        reopening_str = request.POST.get("reopening_date")

        if not all([closure_start_str, closure_end_str, closure_reason, reopening_str]):
            messages.error(request, "❌ Todos los campos obligatorios son requeridos")
        else:
            try:
                closure_start = date.fromisoformat(closure_start_str)
                closure_end = date.fromisoformat(closure_end_str)
                reopening = date.fromisoformat(reopening_str)
            except ValueError:
                messages.error(request, "❌ Fecha inválida")
                return redirect("vacation_closure_form")

            if not parent_emails:
                messages.warning(request, "⚠️ No hay padres con email para enviar")
                return redirect("apps")

            jobs = [
                {
                    "recipients": email_addr,
                    "start_closure_day_name": DIAS_ES[closure_start.weekday()],
                    "start_closure_day_number": closure_start.day,
                    "end_closure_day_name": DIAS_ES[closure_end.weekday()],
                    "end_closure_day_number": closure_end.day,
                    "month_closure": MESES_ES[closure_start.month - 1],
                    "month_closure_end": MESES_ES[closure_end.month - 1],
                    "closure_reason": closure_reason,
                    "reopening_day_name": DIAS_ES[reopening.weekday()],
                    "reopening_day_number": reopening.day,
                    "month_reopening": MESES_ES[reopening.month - 1],
                }
                for email_addr in parent_emails
            ]
            _mass_send(
                request,
                jobs,
                send_vacation_closure_email,
                log_label="Cierre por vacaciones",
                success_text="✅ Aviso de cierre enviado a {count} padre(s)",
                failure_text="⚠️ {count} email(s) no pudieron enviarse",
            )
            return redirect("apps")

    email_html = render_to_string(
        "emails/vacation_closure.html",
        {
            "start_closure_day_name": "lunes",
            "start_closure_day_number": 23,
            "end_closure_day_name": "viernes",
            "end_closure_day_number": 3,
            "month_closure": "diciembre",
            "month_closure_end": "enero",
            "closure_reason": "Navidad",
            "reopening_day_name": "lunes",
            "reopening_day_number": 8,
            "month_reopening": "enero",
        },
    )
    return render(
        request,
        "apps/vacation_closure_form.html",
        {
            "parent_count": parent_count,
            "email_html": email_html,
        },
    )


# ============================================================================
# CERTIFICADO RENTA - Generación y envío
# ============================================================================


@admin_required
def tax_certificate_form(request):
    """
    Vista para generar y enviar certificados fiscales.
    GET: Muestra formulario con año por defecto
    POST: Genera y envía certificados a todos los padres con pagos

    NOTE: this is the ONE mass mail that deliberately does NOT exclude
    waiting-list families or deduplicate addresses — see the docstring of
    `comms.services.email_functions.send_all_tax_certificates`. The certificate
    attests money actually paid, per DNI.
    """

    today = date.today()
    default_year = today.year - 1

    parents_with_payments = (
        Parent.objects.filter(payments__payment_status="completed", payments__payment_date__year=default_year)
        .distinct()
        .count()
    )

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in ("preview", "test_send"):
            _year = _safe_year(request.POST.get("year"), default_year)
            _ctx = {"year": _year, "parent_name": "Nombre del padre"}
            return _preview_or_test(
                action, "tax_certificate", _ctx, f"[TEST] 📋 Certificado de Renta {_year} - Five a Day"
            )

        year = _safe_year(request.POST.get("year"), default_year)
        results = send_all_tax_certificates(year)

        if results["sent"] > 0:
            HistoryLog.log("email_sent", f"Certificado de renta: {results['sent']} email(s) enviados", icon="mail")
            messages.success(request, f"✅ Certificados enviados a {results['sent']} padre(s)")
        if results.get("skipped", 0) > 0:
            messages.info(request, f"ℹ️ {results['skipped']} padre(s) omitidos (sin email)")
        if results.get("failed", 0) > 0:
            # Logged even when nothing was sent: a total SMTP outage used to
            # leave no trace at all in the activity feed.
            if not results["sent"]:
                HistoryLog.log(
                    "email_sent", f"Certificado de renta: 0 enviados, {results['failed']} fallidos", icon="mail"
                )
            messages.warning(request, f"⚠️ {results['failed']} certificado(s) fallaron")
        return redirect("apps")

    email_html = render_to_string(
        "emails/tax_certificate.html",
        {
            "year": default_year,
            "parent_name": "Nombre del padre",
        },
    )
    return render(
        request,
        "apps/tax_certificate_form.html",
        {
            "default_year": default_year,
            "parents_with_payments": parents_with_payments,
            "email_html": email_html,
        },
    )


# ============================================================================
# INFORME MENSUAL - Formulario de envío
# ============================================================================


@admin_required
def monthly_report_form(request):
    """
    Vista para enviar informes mensuales a los padres.
    GET: Muestra formulario con mes/año actual
    POST: Envía informes personalizados a cada padre
    """
    today = date.today()
    current_month = MESES_ES[today.month - 1]

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in ("preview", "test_send"):
            _month = request.POST.get("month", current_month)
            _year = _safe_year(request.POST.get("year"), today.year)
            _ctx = {
                "month": _month,
                "year": _year,
                "parent_name": "Nombre del padre",
                "students": [{"name": "Alumno Ejemplo", "group": "Grupo A"}],
                "total_students": 1,
            }
            return _preview_or_test(
                action, "monthly_report", _ctx, f"[TEST] 📊 Informe Mensual - {_month.title()} {_year}"
            )

        month = request.POST.get("month", current_month)
        year = _safe_year(request.POST.get("year"), today.year)

        jobs = []
        for parent in _mass_mail_parents(with_children=True):
            students_data = [
                {"name": s.full_name, "group": s.group.group_name if s.group else "Sin grupo"}
                for s in parent.active_children
            ]
            jobs.append(
                {
                    "recipient": parent.email,
                    "report_data": {
                        "month": month,
                        "year": year,
                        "parent_name": parent.full_name,
                        "students": students_data,
                        "total_students": len(students_data),
                    },
                }
            )

        _mass_send(
            request,
            jobs,
            send_monthly_report,
            log_label="Informe mensual",
            success_text="✅ Informes enviados a {count} padre(s)",
            failure_text="⚠️ {count} informe(s) no pudieron enviarse",
        )
        return redirect("apps")

    parent_count = len(_parent_recipients())
    # `is_waiting=False` here too: the waiting queue has its own page and its own
    # counter, and counting those entries as students made this figure disagree
    # with every group's `enrolled_count`.
    total_students = Student.objects.filter(active=True, is_waiting=False).count()
    total_groups = Group.objects.filter(active=True).count()

    email_html = render_to_string(
        "emails/monthly_report.html",
        {
            "month": current_month,
            "year": today.year,
            "parent_name": "Nombre del padre",
            "students": [{"name": "Alumno Ejemplo", "group": "Grupo A"}],
            "total_students": 1,
        },
    )
    return render(
        request,
        "apps/monthly_report_form.html",
        {
            "months": MESES_ES,
            "current_month": current_month,
            "current_year": today.year,
            "parent_count": parent_count,
            "total_students": total_students,
            "total_groups": total_groups,
            "email_html": email_html,
        },
    )


# ============================================================================
# BIENVENIDA - Email de alta de estudiante
# ============================================================================


@admin_required
def welcome_form(request):
    """Merged into enrollment_form — redirect all traffic there."""
    return redirect("enrollment_form")


# ============================================================================
# CUMPLEAÑOS - Gestión de emails de cumpleaños
# ============================================================================


@admin_required
def birthday_form(request):
    """
    Vista para gestionar y enviar manualmente emails de cumpleaños.
    GET: Muestra cumpleaños de hoy y del mes, con preview
    POST: Envía manualmente los emails de cumpleaños de hoy
    """
    today = date.today()
    # `is_waiting=False`, like every other mass mail: a waiting-list entry is not
    # a student of the academy yet, and the cron (`send_birthday_emails_task`)
    # makes the same call so the manual path and the daily job cannot disagree
    # about who gets a card.
    birthday_students = (
        Student.objects.filter(birth_date__month=today.month, birth_date__day=today.day, active=True, is_waiting=False)
        .select_related("group")
        .prefetch_related(_EMAILABLE_PARENTS_PREFETCH)
    )

    month_birthdays = (
        Student.objects.filter(birth_date__month=today.month, active=True, is_waiting=False)
        .select_related("group")
        .order_by("birth_date__day")
    )

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in ("preview", "test_send"):
            # One execution: `.first()` plus `.exists()` ran the same query twice.
            _first_birthday = next(iter(birthday_students), None)
            _name = _first_birthday.first_name if _first_birthday else "Alumno Ejemplo"
            return _preview_or_test(
                action,
                "happy_birthday",
                {"name": _name},
                f"[TEST] 🎉 ¡Feliz Cumpleaños {_name}!",
                inline_images={"birthday_image": _birthday_image_path()},
            )

        students_today = list(birthday_students)
        if not students_today:
            messages.info(request, "ℹ️ No hay cumpleaños hoy")
            return redirect("birthday_form")

        image_path = _birthday_image_path()
        jobs = []
        for student in students_today:
            # EVERY emailable parent, not just the first — a birthday card that
            # goes to one of two guardians looks like a snub to the other. The
            # cron's fan-out task does the same; this manual path had kept the
            # single-parent `.first()` bug the task was fixed for. Deduplicated
            # because two guardians can legitimately share one mailbox.
            recipients = _dedupe_emails(p.email for p in student.emailable_parents)
            if not recipients:
                continue
            jobs.append(
                {
                    "template_name": "happy_birthday",
                    "recipients": recipients,
                    "subject": f"🎉 ¡Feliz Cumpleaños {student.first_name}!",
                    "context": {"name": student.first_name},
                    "inline_images": {"birthday_image": image_path},
                }
            )

        if not jobs:
            # There ARE birthdays, but nobody reachable — a distinct outcome from
            # "no birthdays today". The old code reported neither and simply
            # returned a clean success page having sent nothing.
            messages.warning(
                request,
                f"⚠️ Hay {len(students_today)} cumpleaños hoy, pero ningún padre/tutor tiene email registrado.",
            )
            return redirect("birthday_form")

        _mass_send(
            request,
            jobs,
            email_service.send_email,
            log_label="Cumpleaños",
            success_text="✅ Email de cumpleaños enviado a {count} estudiante(s)",
            failure_text="⚠️ {count} email(s) no pudieron enviarse",
        )
        return redirect("birthday_form")

    email_html = render_to_string(
        "emails/happy_birthday.html",
        {
            "name": "Alumno",
        },
    )
    return render(
        request,
        "apps/birthday_form.html",
        {
            "today": today.strftime("%d/%m/%Y"),
            "birthday_students": birthday_students,
            "month_birthdays": month_birthdays,
            "email_html": email_html,
        },
    )


# ============================================================================
# RECIBOS - Generación y envío trimestral/mensual
# ============================================================================


@admin_required
def receipts_form(request):
    """
    Vista para enviar recibos trimestrales (niños) o mensuales (adultos).
    GET: Muestra formulario con opciones de trimestre/mes
    POST: Envía recibos a los padres correspondientes
    """
    today = date.today()
    current_month = MESES_ES[today.month - 1]

    quarter_idx = (today.month - 1) // 3
    quarter_start = quarter_idx * 3
    quarter_months = [MESES_ES[quarter_start], MESES_ES[quarter_start + 1], MESES_ES[quarter_start + 2]]

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in ("preview", "test_send"):
            _rtype = request.POST.get("receipt_type", "quarterly_child")
            if _rtype == "quarterly_child":
                _m1 = request.POST.get("month_1", quarter_months[0])
                _m2 = request.POST.get("month_2", quarter_months[1])
                _m3 = request.POST.get("month_3", quarter_months[2])
                _template = "receipt_quarterly_child"
                _ctx = {"student_name": "Alumno Ejemplo", "month_1": _m1, "month_2": _m2, "month_3": _m3}
                _subject = f"[TEST] 🧾 Recibo Trimestral - {_m1.title()}/{_m2.title()}/{_m3.title()}"
            elif _rtype == "enrollment":
                from billing.models import current_academic_year

                _ay = current_academic_year()
                _template = "receipt_enrollment"
                _ctx = {"student_name": "Alumno Ejemplo", "academic_year": _ay}
                _subject = f"[TEST] 🧾 Recibo Matrícula - {_ay}"
            else:
                _adm = request.POST.get("adult_month", current_month)
                _template = "receipt_adult"
                _ctx = {"month": _adm}
                _subject = f"[TEST] 🧾 Recibo Mensual - {_adm.title()}"
            return _preview_or_test(action, _template, _ctx, _subject)

        receipt_type = request.POST.get("receipt_type", "quarterly_child")

        # `success_count` / `error_count` used to be initialised INSIDE each of
        # these three branches and read after them, so adding a fourth branch
        # raised NameError *after* the emails had already gone out. The branches
        # now only build the batch; `_mass_send` owns the counters and the tally.
        if receipt_type == "quarterly_child":
            month_1 = request.POST.get("month_1", quarter_months[0])
            month_2 = request.POST.get("month_2", quarter_months[1])
            month_3 = request.POST.get("month_3", quarter_months[2])
            sender = send_quarterly_receipt_email
            jobs = [
                {
                    "parent_email": parent.email,
                    "student_name": student.full_name,
                    "month_1": month_1,
                    "month_2": month_2,
                    "month_3": month_3,
                }
                for parent in _mass_mail_parents(with_children=True)
                for student in parent.active_children
            ]
        elif receipt_type == "enrollment":
            from billing.models import current_academic_year

            academic_year = current_academic_year()
            sender = email_service.send_email
            jobs = [
                {
                    "template_name": "receipt_enrollment",
                    "recipients": parent.email,
                    "subject": f"🧾 Recibo Matrícula {academic_year} — {student.full_name}",
                    "context": {"student_name": student.full_name, "academic_year": academic_year},
                }
                for parent in _mass_mail_parents(with_children=True)
                for student in parent.active_children
            ]
        else:
            adult_month = request.POST.get("adult_month", current_month)
            # ADULT students receive their own monthly receipt. This used to
            # query parents of active children — so the adult receipt went to
            # every child's parent and never reached a single adult student.
            adult_students = (
                Student.objects.filter(active=True, is_adult=True, is_waiting=False)
                .exclude(email="")
                .exclude(email__isnull=True)
                .order_by("id")
            )
            seen_adult: set[str] = set()
            sender = email_service.send_email
            jobs = []
            for student in adult_students:
                key = student.email.strip().lower()
                if not key or key in seen_adult:
                    continue
                seen_adult.add(key)
                jobs.append(
                    {
                        "template_name": "receipt_adult",
                        "recipients": student.email,
                        "subject": f"🧾 Recibo Mensual - {adult_month.title()}",
                        "context": {"month": adult_month, "student_name": student.full_name},
                    }
                )

        _mass_send(
            request,
            jobs,
            sender,
            log_label="Recibos",
            success_text="✅ Recibos enviados: {count}",
            failure_text="⚠️ {count} recibo(s) no pudieron enviarse",
        )
        return redirect("apps")

    parent_count = len(_parent_recipients())
    email_html = render_to_string(
        "emails/receipt_quarterly_child.html",
        {
            "student_name": "Alumno Ejemplo",
            "month_1": quarter_months[0],
            "month_2": quarter_months[1],
            "month_3": quarter_months[2],
        },
    )
    return render(
        request,
        "apps/receipts_form.html",
        {
            "months": MESES_ES,
            "current_month": current_month,
            "quarter_months": quarter_months,
            "parent_count": parent_count,
            "email_html": email_html,
        },
    )


# ============================================================================
# NEWSLETTER - Boletín informativo por grupo
# ============================================================================


@admin_required
def newsletter_form(request):
    """
    Vista para enviar newsletters por grupo.
    GET: Muestra formulario con selector de grupo
    POST: Envía newsletter a todos los padres con estudiantes activos
    """
    groups = Group.objects.filter(active=True).order_by("group_name")

    if request.method == "POST":
        action = request.POST.get("action", "")
        group_name = request.POST.get("group_name", "")
        newsletter_link = request.POST.get("newsletter_link", "")
        message_text = strip_tags(request.POST.get("message", ""))

        if action in ("preview", "test_send"):
            _ctx = {
                "group_name": group_name,
                "newsletter_link": newsletter_link,
                "message": message_text,
            }
            return _preview_or_test(action, "newsletter", _ctx, f"[TEST] 📰 Newsletter {group_name} - Five a Day")

        if not group_name:
            messages.error(request, "❌ Debes seleccionar un grupo")
            return redirect("newsletter_form")

        # Send to parents with students in the selected group. If the group
        # can't be found (renamed / deactivated between page load and submit)
        # this used to silently fall back to EVERY parent while keeping the
        # group's name in the subject — a mass send nobody asked for.
        group_obj = Group.objects.filter(group_name=group_name, active=True).first()
        if not group_obj:
            messages.error(
                request,
                f"❌ El grupo «{group_name}» ya no existe o está inactivo. No se ha enviado nada.",
            )
            return redirect("newsletter_form")

        parent_emails = _parent_recipients(group=group_obj)
        if not parent_emails:
            messages.warning(request, "⚠️ No hay padres con email en este grupo")
            return redirect("apps")

        jobs = [
            {
                "template_name": "newsletter",
                "recipients": email_addr,
                "subject": f"📰 Newsletter {group_name} - Five a Day",
                "context": {
                    "group_name": group_name,
                    "newsletter_link": newsletter_link,
                    "message": message_text,
                },
            }
            for email_addr in parent_emails
        ]
        _mass_send(
            request,
            jobs,
            email_service.send_email,
            log_label=f"Newsletter {group_name}",
            success_text=f"✅ Newsletter enviada a {{count}} padre(s) del grupo {group_name}",
            failure_text="⚠️ {count} email(s) no pudieron enviarse",
        )
        return redirect("apps")

    email_html = render_to_string(
        "emails/newsletter.html",
        {
            "group_name": "Grupo Ejemplo",
            "newsletter_link": "https://canva.com/...",
            "message": "",
        },
    )
    return render(
        request,
        "apps/newsletter_form.html",
        {
            "groups": groups,
            "parent_count": len(_parent_recipients()),
            "email_html": email_html,
        },
    )


# ============================================================================
# MATRÍCULAS - Confirmación de matrícula
# ============================================================================


@admin_required
def enrollment_form(request):
    """
    Vista para enviar confirmación de matrícula manualmente.
    GET: Muestra formulario con selector de estudiante
    POST: Envía confirmación al padre del estudiante seleccionado
    """
    from billing.models import academic_year_for_month

    today = date.today()
    current_month = MESES_ES[today.month - 1]
    # Waiting-list entries are not enrolled — offering them in a
    # "confirmación de matrícula" picker is offering to confirm a matriculation
    # that does not exist.
    students = (
        Student.objects.filter(active=True, is_waiting=False)
        .select_related("group")
        .order_by("last_name", "first_name")
    )

    # The academic year the CURRENT teaching month belongs to. This was a
    # hand-rolled September-rollover that disagreed with the helper every
    # May–August, so the matriculation-confirmation email named a different
    # course from the enrollment record it was confirming.
    default_academic_year = academic_year_for_month(today)

    if request.method == "POST":
        action = request.POST.get("action", "")
        email_type = request.POST.get("email_type", "enrollment")

        if action in ("preview", "test_send"):
            _student_id = request.POST.get("student_id")
            if email_type == "welcome":
                _ctx = {
                    "parent_name": "Nombre del padre",
                    "student_name": "Nombre del alumno",
                    "group_name": "Grupo A",
                    "enrollment_type": "Mensual",
                    "schedule_type": "Jornada completa",
                    "start_date": "01/09/2025",
                }
                if _student_id:
                    try:
                        _s = Student.objects.select_related("group").get(id=_student_id)
                        # Same resolution the send uses — see `_emailable_parent`.
                        _p = _emailable_parent(_s)
                        _ctx["student_name"] = _s.full_name
                        if _p:
                            _ctx["parent_name"] = _p.full_name
                        if _s.group:
                            _ctx["group_name"] = _s.group.group_name
                    except Exception:
                        # Preview/test-send only: a stale student id just means
                        # the placeholder names stay in `_ctx`. Never block the
                        # preview over it.
                        pass
                return _preview_or_test(
                    action,
                    "welcome_student",
                    _ctx,
                    f"[TEST] 🎉 Bienvenida a Five a Day - {_ctx['student_name']}",
                )

            _etype = request.POST.get("enrollment_type", "child")
            _gender = request.POST.get("gender", "m")
            _ay = request.POST.get("academic_year", default_academic_year)
            _month = request.POST.get("month", current_month)
            _student_name = "Alumno Ejemplo"
            if _student_id:
                try:
                    _s = Student.objects.get(id=_student_id)
                    _student_name = _s.full_name
                except Exception:
                    # Preview/test-send only: fall back to the placeholder
                    # name if the student id no longer resolves.
                    pass
            _template = "enrollment_child" if _etype == "child" else "enrollment_adult"
            _ctx = {"student": _student_name, "genero": _gender, "academic_year": _ay, "month": _month}
            return _preview_or_test(action, _template, _ctx, f"[TEST] 🎉 Confirmación de Matrícula - {_student_name}")

        student_id = request.POST.get("student_id")
        if not student_id:
            messages.error(request, "❌ Selecciona un estudiante")
        elif email_type == "welcome":
            try:
                student = Student.objects.select_related("group").get(id=student_id)
                parent = _emailable_parent(student)
                if not parent:
                    messages.error(request, f"❌ {student.full_name} no tiene padre con email registrado")
                else:
                    result = send_welcome_email(
                        parent_email=parent.email,
                        parent_name=parent.full_name,
                        student_name=student.full_name,
                        group_name=student.group.group_name if student.group else None,
                    )
                    if result:
                        HistoryLog.log("email_sent", f"Bienvenida: 1 email enviado ({student.full_name})", icon="mail")
                        messages.success(request, f"✅ Email de bienvenida enviado a {parent.email}")
                    else:
                        messages.error(request, "❌ Error al enviar el email")
            except Student.DoesNotExist:
                messages.error(request, "❌ Estudiante no encontrado")
        else:
            enrollment_type = request.POST.get("enrollment_type", "child")
            gender = request.POST.get("gender", "m")
            academic_year = request.POST.get("academic_year", default_academic_year)
            month = request.POST.get("month", current_month)
            try:
                student = Student.objects.get(id=student_id)
                parent = _emailable_parent(student)
                if not parent:
                    messages.error(request, f"❌ {student.full_name} no tiene padre con email registrado")
                else:
                    template = "enrollment_child" if enrollment_type == "child" else "enrollment_adult"
                    result = email_service.send_email(
                        template_name=template,
                        recipients=parent.email,
                        subject=f"🎉 Confirmación de Matrícula - {student.full_name}",
                        context={
                            "student": student.full_name,
                            "genero": gender,
                            "academic_year": academic_year,
                            "month": month,
                        },
                    )
                    if result:
                        HistoryLog.log(
                            "email_sent", f"Confirmación matrícula: 1 email enviado ({student.full_name})", icon="mail"
                        )
                        messages.success(request, f"✅ Confirmación de matrícula enviada a {parent.email}")
                    else:
                        messages.error(request, "❌ Error al enviar el email")
            except Student.DoesNotExist:
                messages.error(request, "❌ Estudiante no encontrado")
        return redirect("enrollment_form")

    email_html = render_to_string(
        "emails/welcome_student.html",
        {
            "parent_name": "Nombre del padre",
            "student_name": "Nombre del alumno",
            "group_name": "Grupo A",
            "enrollment_type": "Mensual",
            "schedule_type": "Jornada completa",
            "start_date": "01/09/2025",
        },
    )
    return render(
        request,
        "apps/enrollment_form.html",
        {
            "students": students,
            "months": MESES_ES,
            "current_month": current_month,
            "default_academic_year": default_academic_year,
            "email_html": email_html,
        },
    )
