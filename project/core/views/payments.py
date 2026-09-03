import csv
import json
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, DecimalField, Max, Min, Q, Sum, Value, When
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from billing import constants
from billing.models import Payment
from core.constants import MESES_ES
from core.decorators import admin_required
from core.models import HistoryLog
from core.utils import MAX_QUERY_YEAR, MIN_QUERY_YEAR, csv_safe_row, safe_int
from students.models import Parent, Student

logger = logging.getLogger(__name__)

# Max payment rows sent to the browser in one page load. The table paginates
# client-side, so everything returned is held in the DOM.
_LIST_CAP = 1000


def parse_date_value(date_value):
    """Parse date strings supporting dd/mm/yyyy and yyyy-mm-dd formats."""
    if not date_value:
        return None
    if isinstance(date_value, date):
        return date_value

    raw_value = str(date_value).strip()
    if not raw_value:
        return None

    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw_value, fmt).date()
        except ValueError:
            continue

    raise ValidationError(f"Formato de fecha inválido: '{raw_value}'. Usa dd/mm/yyyy.")


def _queue_payment_receipt(payment_id: int) -> None:
    """Fire the payment-receipt email for a payment just marked completed.

    Dispatched ON COMMIT, not inline. Production runs
    `CELERY_TASK_ALWAYS_EAGER=True` (Cloud Run, no worker), so `.delay()` executes
    the task *here* — it re-reads the payment by id and emails a receipt. Called
    from inside a `transaction.atomic()` block that later rolls back, that is a
    receipt for money the database does not record; with a real broker it is a
    worker reading the row before the write is visible. `transaction.on_commit`
    runs the callback immediately when there is no open transaction, so callers
    outside one (the admin bulk action) behave exactly as before.

    Best-effort: a receipt that fails to send must never fail the request that
    recorded the money. Imported lazily to avoid a comms->billing import cycle.
    """

    def _dispatch():
        try:
            from comms.tasks import send_payment_receipt_email_task

            send_payment_receipt_email_task.delay(int(payment_id))
        except Exception:  # noqa: BLE001 — receipt is nice-to-have
            logger.exception("Failed to enqueue payment receipt for payment %d", int(payment_id))

    transaction.on_commit(_dispatch)


def _validated_choice(value, choices, default):
    """Return `value` when it is a valid choice key, otherwise `default`.

    `Model.objects.create()` does not enforce `choices`, so raw POST values
    reached the DB unchecked and `get_..._display()` then echoed them back.
    """
    return value if value in {key for key, _ in choices} else default


def _filtered_payments(request, default_year=None):
    """The payments queryset for the CURRENT request's filters.

    Shared by `payments_list` and `export_payments`, which is the whole point:
    "Exportar" used to run `Payment.objects.all()` and hand back the entire
    table, so a filtered screen produced an unfiltered file — reconciled against
    the figures on that screen it looks like the totals are wrong, when it is the
    file that is answering a different question.

    Returns `(queryset, search_query, selected_month, selected_year)`.

    `default_year` is what to use when the request carries no `year`: the list
    view defaults to the current year (a page has to show *something*), the
    export passes None and exports every year. That asymmetry is deliberate —
    the export link is a plain anchor, so a request with no parameters is the
    "I want everything" case, and silently narrowing it to this year would drop
    rows that the caller never asked to exclude.
    """
    payments_queryset = Payment.objects.select_related(
        "student", "parent", "enrollment", "enrollment__enrollment_type"
    ).order_by("-due_date", "-created_at")

    search_query = request.GET.get("search", "")
    if search_query:
        payments_queryset = payments_queryset.filter(
            Q(student__first_name__icontains=search_query)
            | Q(student__last_name__icontains=search_query)
            | Q(parent__first_name__icontains=search_query)
            | Q(parent__last_name__icontains=search_query)
            | Q(concept__icontains=search_query)
            | Q(reference_number__icontains=search_query)
        )

    # Month/year filter (server-side, so it narrows the whole dataset rather
    # than only the rows already rendered). Empty month = the whole year.
    selected_month = safe_int(request.GET.get("month"), default=None, low=1, high=12)
    selected_year = safe_int(request.GET.get("year"), default=default_year, low=MIN_QUERY_YEAR, high=MAX_QUERY_YEAR)
    if selected_year:
        payments_queryset = payments_queryset.filter(due_date__year=selected_year)
    if selected_month:
        payments_queryset = payments_queryset.filter(due_date__month=selected_month)

    return payments_queryset, search_query, selected_month, selected_year


@admin_required
def payments_list(request):
    """
    Main payments list view with pagination
    Shows active payments only (not deactivated ones)
    """
    today = date.today()
    current_year = today.year

    payments_queryset, search_query, selected_month, selected_year = _filtered_payments(
        request, default_year=current_year
    )

    # Years that actually have payments, so the dropdown never offers an
    # empty year. Always includes the current one.
    #
    # Min/Max over `due_date` rather than `DISTINCT EXTRACT(YEAR FROM due_date)`:
    # the latter cannot use the `due_date` index and sequentially scanned the whole
    # payments table on every page load. Min/Max are index-only lookups. The span
    # between the first and last payment is contiguous in practice, and offering a
    # year with no rows is harmless — it renders an empty table, which is exactly
    # what the old query was avoiding at the cost of a full scan.
    span = Payment.objects.aggregate(first=Min("due_date"), last=Max("due_date"))
    years = {current_year}
    if span["first"] and span["last"]:
        years |= set(range(span["first"].year, span["last"].year + 1))
    year_choices = sorted(years, reverse=True)

    # Summary figures describe the SELECTED period, not "today", so the totals
    # always match the rows on screen. Previously "Esperado"/"Cobrado" were
    # hard-wired to the current month while "Pendiente"/"Vencido" were all-time
    # — three different periods in one line, which is a large part of why
    # "esperado" was reported as unintelligible.
    _zero = Decimal("0.00")
    # Same filters as the rows — INCLUDING the search. Rebuilding from
    # `Payment.objects` without it meant `?search=Ana` showed Ana's two payments
    # under academy-wide totals ("2 resultados" next to a 214-payment summary),
    # which contradicts the very comment above.
    period_payments = payments_queryset

    stats = period_payments.aggregate(
        # Money still expected in this period. Cancelled / failed / refunded are
        # excluded: cancelling a duplicate used to leave it counted as revenue
        # you were still waiting for.
        expected_total=Sum(
            Case(
                When(payment_status__in=constants.LIVE_PAYMENT_STATUSES, then="amount"),
                default=Value(0),
                output_field=DecimalField(),
            )
        ),
        expected_count=Sum(
            Case(
                When(payment_status__in=constants.LIVE_PAYMENT_STATUSES, then=Value(1)),
                default=Value(0),
            )
        ),
        completed_total=Sum(
            Case(
                When(payment_status="completed", then="amount"),
                default=Value(0),
                output_field=DecimalField(),
            )
        ),
        completed_count=Sum(
            Case(
                When(payment_status="completed", then=Value(1)),
                default=Value(0),
            )
        ),
        pending_total=Sum(
            Case(
                When(payment_status="pending", then="amount"),
                default=Value(0),
                output_field=DecimalField(),
            )
        ),
        pending_count=Sum(
            Case(
                When(payment_status="pending", then=Value(1)),
                default=Value(0),
            )
        ),
        overdue_total=Sum(
            Case(
                When(payment_status="pending", due_date__lt=today, then="amount"),
                default=Value(0),
                output_field=DecimalField(),
            )
        ),
        overdue_count=Sum(
            Case(
                When(payment_status="pending", due_date__lt=today, then=Value(1)),
                default=Value(0),
            )
        ),
    )

    # Hard cap on rows sent to the browser (the table paginates client-side).
    # `result_truncated` below surfaces it instead of silently dropping rows.
    total_count = payments_queryset.count()
    all_payments_list = list(payments_queryset[:_LIST_CAP])

    context = {
        "payments_list": all_payments_list,
        "total_count": total_count,
        "search_query": search_query,
        "expected_payments_total": stats["expected_total"] or _zero,
        "expected_payments_count": stats["expected_count"] or 0,
        "completed_payments_total": stats["completed_total"] or _zero,
        "completed_payments_count": stats["completed_count"] or 0,
        "pending_payments_total": stats["pending_total"] or _zero,
        "pending_payments_count": stats["pending_count"] or 0,
        "overdue_payments_total": stats["overdue_total"] or _zero,
        "overdue_payments_count": stats["overdue_count"] or 0,
        "payment_method_choices": constants.PAYMENT_METHOD_CHOICES,
        # Month/year filter controls
        "month_choices": list(enumerate(MESES_ES, start=1)),
        "selected_month": selected_month,
        "selected_year": selected_year,
        "period_label": MESES_ES[selected_month - 1].capitalize() if selected_month else "",
        "year_choices": year_choices,
        "result_truncated": total_count > _LIST_CAP,
        "list_cap": _LIST_CAP,
    }

    return render(request, "payments/payments_list.html", context)


@require_http_methods(["GET", "POST"])
@admin_required
def create_payment(request):
    """
    Create new payment
    """

    if request.method == "POST":
        try:
            # Get form data
            student_id = request.POST.get("student_id")
            parent_id = request.POST.get("parent_id")

            # Validate student exists
            student = get_object_or_404(Student, id=student_id)

            # Parent is optional for adult students (they have no parent/tutor).
            # For everyone else a parent is required and must be related.
            parent = None
            if parent_id:
                parent = get_object_or_404(Parent, id=parent_id)
                if not student.parents.filter(id=parent_id).exists():
                    messages.error(
                        request,
                        "El padre/tutor seleccionado no está asociado con este estudiante.",
                    )
                    return redirect("payments_list")
            elif not student.is_adult:
                messages.error(
                    request,
                    "Debe seleccionar un padre/tutor para este estudiante.",
                )
                return redirect("payments_list")

            # Prefer the ACTIVE enrollment. `enrollments.first()` has no
            # ordering and no status filter, so for a returning student it
            # picked whichever row happened to come first — usually the old
            # finished one — and attached the payment to it.
            enrollment = (
                student.enrollments.filter(status="active").order_by("-enrollment_date", "-id").first()
                or student.enrollments.order_by("-enrollment_date", "-id").first()
            )

            # Choice fields are not validated by Model.objects.create(), so a
            # crafted or stale form could persist e.g. payment_status="wat",
            # which then renders raw through get_payment_status_display().
            payment_type = _validated_choice(
                request.POST.get("payment_type"), constants.PAYMENT_TYPE_CHOICES, "monthly"
            )
            payment_method = _validated_choice(
                request.POST.get("payment_method"), constants.PAYMENT_METHOD_CHOICES, "transfer"
            )
            payment_status = _validated_choice(
                request.POST.get("payment_status"), constants.PAYMENT_STATUS_CHOICES, "pending"
            )

            concept = (request.POST.get("concept") or "").strip()[:200]

            # Create payment
            payment = Payment(
                student=student,
                parent=parent,
                enrollment=enrollment,
                payment_type=payment_type,
                payment_method=payment_method,
                amount=Decimal(str(request.POST.get("amount", "0"))),
                currency=request.POST.get("currency", "EUR")[:3],
                payment_status=payment_status,
                due_date=parse_date_value(request.POST.get("due_date")),
                payment_date=parse_date_value(request.POST.get("payment_date")),
                concept=concept,
                reference_number=(request.POST.get("reference_number", "") or "")[:50],
                observations=request.POST.get("observations", ""),
            )
            # One transaction for the payment, its history entry and the receipt
            # dispatch. Without it a HistoryLog failure showed the admin "Error al
            # crear el pago" while the Payment was already committed — and the
            # obvious response to that message is to submit the form again, which
            # created a second one. `_queue_payment_receipt` defers to COMMIT, so
            # no receipt is emailed for a payment that rolled back.
            with transaction.atomic():
                payment.full_clean(exclude=["enrollment"])
                payment.save()
                HistoryLog.log(
                    "payment_created",
                    f"Pago creado: {student.full_name} — €{payment.amount} ({payment.get_payment_type_display()})",
                    icon="add_card",
                )
                if payment.payment_status == "completed":
                    _queue_payment_receipt(payment.id)
            messages.success(request, f"Pago creado exitosamente para {student.full_name}.")
            return redirect("payments_list")

        except ValidationError as e:
            # ValidationError.messages is Django's written-for-humans text.
            messages.error(request, " ".join(e.messages))
            return redirect("payments_list")
        except InvalidOperation:
            logger.exception("Invalid amount submitted when creating a payment")
            messages.error(request, "El importe introducido no es válido.")
            return redirect("payments_list")
        except IntegrityError as e:
            # `full_clean()` above validates the constraint and raises ValidationError
            # with its own Spanish message, so this branch only catches the RACE: two
            # requests that both passed validation before either inserted. Matched on
            # the constraint name so a genuinely different IntegrityError still gets
            # the generic message rather than a confidently wrong one. Nothing from
            # the exception is echoed to the client.
            logger.exception("IntegrityError while creating a payment")
            if "unique_pending_periodic_payment_per_month" in str(e):
                messages.error(
                    request,
                    "Ya existe un pago pendiente de ese tipo para ese alumno en ese mes. "
                    "Edita o cancela el pago existente en lugar de crear otro.",
                )
            else:
                messages.error(request, "Error al crear el pago. Revisa los datos e inténtalo de nuevo.")
            return redirect("payments_list")
        except Exception:
            # Never echo str(e): on an IntegrityError it leaks the table and
            # column names, on a DataError the column width.
            logger.exception("Error creating payment")
            messages.error(request, "Error al crear el pago. Revisa los datos e inténtalo de nuevo.")
            return redirect("payments_list")

    return render(request, "payments/payment_create.html", {})


@admin_required
def payment_detail_view(request, payment_id):
    """
    Detailed view of a payment (read-only)
    """
    # The template walks student (+ group), parent, enrollment and its type —
    # unjoined that page cost 6 queries for one row.
    payment = get_object_or_404(
        Payment.objects.select_related(
            "student", "student__group", "parent", "enrollment", "enrollment__enrollment_type"
        ),
        id=payment_id,
    )

    context = {
        "payment": payment,
    }

    return render(request, "payments/payment_detail.html", context)


@require_http_methods(["GET"])
@admin_required
def payment_receipt_pdf(request, payment_id):
    """Stream a payment-receipt PDF (v1.3)."""
    from billing.services.pdf_service import generate_payment_receipt

    payment = get_object_or_404(Payment.objects.select_related("student", "parent"), id=payment_id)
    pdf_bytes = generate_payment_receipt(payment)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="recibo-{payment.id}.pdf"'
    return response


@require_http_methods(["GET"])
@admin_required
def student_payments_pdf(request, student_id):
    """Stream a student's full payment history as a PDF.

    Shows the payment method and both dates per row, so the academy can see at
    a glance when each fee was paid and how. `?year=` narrows to one academic
    calendar year; omitted means every payment on record.
    """
    from billing.services.pdf_service import generate_student_payment_history

    student = get_object_or_404(Student.objects.select_related("group"), id=student_id)

    payments = Payment.objects.filter(student=student).select_related("enrollment").order_by("due_date", "id")

    year = safe_int(request.GET.get("year"), default=None, low=MIN_QUERY_YEAR, high=MAX_QUERY_YEAR)
    suffix = ""
    if year:
        payments = payments.filter(due_date__year=year)
        suffix = str(year)

    pdf_bytes = generate_student_payment_history(student, payments, title_suffix=suffix)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    slug = student.full_name.replace(" ", "-").lower()
    stem = f"pagos-{slug}" + (f"-{year}" if year else "")
    response["Content-Disposition"] = f'attachment; filename="{stem}.pdf"'
    return response


@require_http_methods(["POST"])
@admin_required
def update_payment(request, payment_id):
    """
    AJAX endpoint to update existing payment
    """
    # Outside the try: Http404 subclasses Exception, so inside it the catch-all
    # converted a plain missing row into a 500 + traceback in the logs.
    payment = get_object_or_404(Payment, id=payment_id)
    try:
        was_completed = payment.payment_status == "completed"

        # Parse data
        if request.content_type == "application/json":
            data = json.loads(request.body)
        else:
            data = request.POST

        # Update fields
        if "student_id" in data:
            student = get_object_or_404(Student, id=data["student_id"])
            payment.student = student
        if "parent_id" in data:
            # An empty parent_id clears the link (valid for adult students),
            # rather than 404-ing on a lookup for "".
            if data["parent_id"] in (None, "", 0, "0"):
                payment.parent = None
            else:
                payment.parent = get_object_or_404(Parent, id=data["parent_id"])
        # Stricter than Payment.clean() on purpose: the model exempts adults
        # (who normally have no parent at all), but if a parent IS named on the
        # payment they must actually be linked to the student — otherwise the
        # receipt would be issued to someone unrelated.
        if (
            payment.student_id
            and payment.parent_id
            and not payment.student.parents.filter(id=payment.parent_id).exists()
        ):
            raise ValidationError("El padre/tutor seleccionado no está asociado con este estudiante.")
        if "payment_type" in data:
            payment.payment_type = data["payment_type"]
        if "payment_method" in data:
            payment.payment_method = data["payment_method"]
        if "amount" in data:
            payment.amount = Decimal(data["amount"])
        if "currency" in data:
            payment.currency = data["currency"]
        if "payment_status" in data:
            payment.payment_status = data["payment_status"]
        if "due_date" in data:
            payment.due_date = parse_date_value(data["due_date"])
        if "payment_date" in data:
            payment.payment_date = parse_date_value(data["payment_date"])
        if "concept" in data:
            payment.concept = data["concept"]
        if "reference_number" in data:
            payment.reference_number = data["reference_number"]
        if "observations" in data:
            payment.observations = data["observations"]

        # Model.save() does NOT call clean(), so without this the endpoint could
        # mark a payment "completed" with payment_date=None — and every income
        # figure filters on payment_date, so the money silently vanished from
        # all reporting. Payment.clean() backfills the date and rejects choices.
        payment.full_clean(exclude=["enrollment", "parent", "student"])
        payment.save()

        # Only on the pending -> completed transition, so an edit to an
        # already-completed payment doesn't re-send the receipt.
        if payment.payment_status == "completed" and not was_completed:
            _queue_payment_receipt(payment.id)

        if request.content_type != "application/json":
            messages.success(request, "Pago actualizado exitosamente.")
            return redirect("payments_list")

        return JsonResponse(
            {
                "success": True,
                "message": "Pago actualizado exitosamente.",
                "payment": {
                    "id": payment.id,
                    "payment_status": payment.get_payment_status_display(),
                    "amount": str(payment.amount),
                },
            }
        )

    except InvalidOperation:
        # A Decimal parse failure. Its str() is internal noise ("[<class
        # 'decimal.ConversionSyntax'>]"), useless to the user and the last
        # exception text still reaching a response from this view.
        logger.exception("Invalid amount submitted for payment %d", int(payment_id))
        error_msg = "El importe introducido no es válido."
        if request.content_type == "application/json":
            return JsonResponse({"success": False, "error": error_msg}, status=400)
        messages.error(request, error_msg)
        return redirect("payments_list")
    except ValidationError as e:
        # `messages` is Django's user-facing validation text -- written to be
        # shown, unlike a raw exception repr.
        error_msg = " ".join(e.messages)
        if request.content_type == "application/json":
            return JsonResponse({"success": False, "error": error_msg}, status=400)
        messages.error(request, error_msg)
        return redirect("payments_list")
    except Http404:
        # The student_id/parent_id lookups above — a missing row is a 404, not a
        # server error to be swallowed by the catch-all.
        raise
    except IntegrityError as e:
        # `full_clean(exclude=["student", ...])` SKIPS the unique-month constraint:
        # Django's UniqueConstraint.validate() returns early when any of its
        # expressions references an excluded field, and this one is keyed on
        # F("student"). So an edit that moves a due date into an occupied month
        # only surfaces here, at the database. Same actionable message
        # create_payment gives; anything else stays generic.
        logger.exception("IntegrityError while updating payment %d", int(payment_id))
        if "unique_pending_periodic_payment_per_month" in str(e):
            error_msg = (
                "Ya existe un pago pendiente de ese tipo para ese alumno en ese mes. "
                "Edita o cancela el pago existente en lugar de crear otro."
            )
        else:
            error_msg = "Error al actualizar el pago. Inténtalo de nuevo."
        if request.content_type == "application/json":
            return JsonResponse({"success": False, "error": error_msg}, status=400)
        messages.error(request, error_msg)
        return redirect("payments_list")
    except Exception:
        logger.exception("Error updating payment %d", int(payment_id))
        error_msg = "Error al actualizar el pago. Inténtalo de nuevo."
        if request.content_type == "application/json":
            return JsonResponse({"success": False, "error": error_msg}, status=500)
        messages.error(request, error_msg)
        return redirect("payments_list")


@require_http_methods(["POST"])
@admin_required
def delete_payment(request, payment_id):
    """
    AJAX endpoint to delete payment
    """
    # Outside the try — a double-clicked delete used to 500 ("Error al eliminar")
    # on the second request while the first had already succeeded.
    payment = get_object_or_404(Payment.objects.select_related("student"), id=payment_id)
    try:
        student_name = payment.student.full_name

        payment.delete()

        return JsonResponse(
            {
                "success": True,
                "message": f"Pago de {student_name} eliminado exitosamente.",
            }
        )

    except Exception:
        logger.exception("Error deleting payment %d", int(payment_id))

        return JsonResponse(
            {"success": False, "error": "Error al eliminar el pago."},
            status=500,
        )


# Soft delete!
@require_http_methods(["POST"])
@admin_required
def deactivate_payment(request, payment_id):
    """
    Soft delete - deactivate payment instead of deleting
    """
    payment = get_object_or_404(Payment, id=payment_id)
    try:
        payment.payment_status = "cancelled"
        payment.save()

        return JsonResponse({"success": True, "message": "Pago desactivado exitosamente."})

    except Exception:
        logger.exception("Error deactivating payment %d", int(payment_id))
        return JsonResponse(
            {"success": False, "message": "Error al desactivar el pago."},
            status=400,
        )


@require_http_methods(["POST"])
@admin_required
def quick_complete_payment(request, payment_id):
    """
    AJAX endpoint to quickly complete a payment by setting its payment method.
    Expects JSON body: {"payment_method": "cash"|"transfer"|"credit_card"}
    """
    payment = get_object_or_404(Payment.objects.select_related("student"), id=payment_id)
    try:
        data = json.loads(request.body)
        payment_method = data.get("payment_method")

        if payment_method not in dict(constants.PAYMENT_METHOD_CHOICES):
            return JsonResponse(
                {"success": False, "error": "Método de pago no válido"},
                status=400,
            )

        # Idempotent: re-completing an already-completed payment used to rewrite
        # its historical payment_date to today, silently moving the money into a
        # different month in every income report (and re-sending the receipt).
        if payment.payment_status == "completed":
            return JsonResponse(
                {
                    "success": True,
                    "already_completed": True,
                    "message": f"El pago de {payment.student.full_name} ya estaba completado.",
                }
            )

        # A cancelled or REFUNDED payment stays visible for the audit trail but is
        # dead. The guard is `Payment.assert_completable()` — the model's own rule,
        # which `Payment.clean()` also runs — rather than a status list retyped
        # here. That list is exactly what went wrong: it named only `cancelled`,
        # and `payment.save()` never runs `clean()`, so a refunded payment was
        # re-completed, its `payment_date` rewritten to today (re-booking money
        # already returned into this month's income) and a receipt emailed for a
        # refund. Passing the loaded status explicitly answers before the row is
        # mutated, so the message can be actionable instead of a 500.
        payment.assert_completable(previous_status=payment.payment_status)

        # One transaction for the status change and its history entry: a
        # HistoryLog failure used to return "Error al completar el pago" with the
        # payment already completed, and `_queue_payment_receipt` had already
        # fired — so a retry either did nothing or, worse, the operator assumed
        # the money was not recorded. The receipt now dispatches on COMMIT.
        with transaction.atomic():
            payment.payment_method = payment_method
            payment.payment_status = "completed"
            payment.payment_date = date.today()
            payment.save()

            HistoryLog.log(
                "payment_completed",
                f"Pago completado: {payment.student.full_name} — "
                f"{payment.get_payment_method_display()} (€{payment.amount})",
                icon="paid",
            )

            # Email the payer a receipt. Previously only the Stripe webhook did
            # this, so a payment taken in cash / by transfer and marked complete
            # here sent nothing at all.
            _queue_payment_receipt(payment.id)

        return JsonResponse(
            {
                "success": True,
                "message": f"Pago de {payment.student.full_name} completado ({payment.get_payment_method_display()}).",
            }
        )

    except ValidationError as e:
        # `ValidationError.messages` is Django's (and the model's) written-for-humans
        # text — the resurrection refusal above arrives here.
        return JsonResponse({"success": False, "error": " ".join(e.messages)}, status=400)
    except Exception:
        logger.exception("Error completing payment %d", int(payment_id))
        return JsonResponse(
            {"success": False, "error": "Error al completar el pago."},
            status=500,
        )


@require_http_methods(["GET"])
@admin_required
def get_payment_details(request, payment_id):
    """
    AJAX endpoint to get payment details
    """
    payment = get_object_or_404(Payment.objects.select_related("student", "parent"), id=payment_id)
    try:
        return JsonResponse(
            {
                "success": True,
                "payment": {
                    "id": payment.id,
                    "student_id": payment.student_id,
                    "student_name": payment.student.full_name,
                    # `_id` reads the FK column already on the row; `payment.parent.id`
                    # fetched the whole related object just to return its pk.
                    "parent_id": payment.parent_id,
                    "parent_name": payment.parent.full_name if payment.parent else "",
                    "enrollment_id": payment.enrollment_id,
                    "payment_type": payment.payment_type,
                    "payment_method": payment.payment_method,
                    "amount": str(payment.amount),
                    "currency": payment.currency,
                    "payment_status": payment.payment_status,
                    "due_date": (payment.due_date.strftime("%Y-%m-%d") if payment.due_date else ""),
                    "payment_date": (payment.payment_date.strftime("%Y-%m-%d") if payment.payment_date else ""),
                    "concept": payment.concept,
                    "reference_number": payment.reference_number,
                    "observations": payment.observations,
                    "created_at": payment.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                },
            }
        )

    except Exception:
        logger.exception("Error fetching payment details for %d", int(payment_id))
        return JsonResponse(
            {
                "success": False,
                "error": "Error al obtener los detalles del pago.",
            },
            status=500,
        )


@require_http_methods(["GET"])
@admin_required
def search_payments(request):
    """
    AJAX endpoint to search payments
    """
    query = request.GET.get("q", "").strip()

    if len(query) < 2:
        return JsonResponse({"results": []})

    payments = (
        Payment.objects.filter(
            Q(student__first_name__icontains=query)
            | Q(student__last_name__icontains=query)
            | Q(parent__first_name__icontains=query)
            | Q(parent__last_name__icontains=query)
            | Q(concept__icontains=query)
            | Q(reference_number__icontains=query)
        )
        .select_related("student", "parent", "enrollment")
        .order_by("-created_at")[:10]
    )

    results = []
    for payment in payments:
        results.append(
            {
                "id": payment.id,
                "student_name": payment.student.full_name,
                # Adult students have no parent — Payment.parent is nullable.
                "parent_name": payment.parent.full_name if payment.parent else "",
                "amount": str(payment.amount),
                "currency": payment.currency,
                "payment_type": payment.get_payment_type_display(),
                "payment_status": payment.get_payment_status_display(),
                "due_date": (payment.due_date.strftime("%Y-%m-%d") if payment.due_date else ""),
                "payment_date": (payment.payment_date.strftime("%Y-%m-%d") if payment.payment_date else ""),
                "concept": payment.concept,
                "reference_number": payment.reference_number,
            }
        )

    return JsonResponse({"results": results})


@require_http_methods(["GET"])
@admin_required
def export_payments(request):
    """Export payments to CSV, honouring the payments list's own filters.

    `search`, `month` and `year` are read exactly as `payments_list` reads them,
    so the file matches the screen it was downloaded from. It used to export
    `Payment.objects.all()` regardless: clicking "Exportar" on a filtered view —
    one month, one family — silently downloaded the whole table, and the totals
    in it agreed with nothing on the page.

    Two things are still NOT honoured, because they never reach the server:
    the status and type filters on that page are applied client-side to rows
    already in the DOM. Add them to the export link's query string and they will
    be picked up here only once the view learns them; today they are absent from
    `request.GET`.
    """
    response = HttpResponse(content_type="text/csv")

    payments, _search, selected_month, selected_year = _filtered_payments(request, default_year=None)

    # Name the file after what is in it, so two exports of different filters do
    # not overwrite each other in the Downloads folder.
    stem = "pagos"
    if selected_year:
        stem += f"-{selected_year}"
        if selected_month:
            stem += f"-{selected_month:02d}"
    response["Content-Disposition"] = f'attachment; filename="{stem}.csv"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "ID",
            "Estudiante",
            "Padre/Tutor",
            "Concepto",
            "Cantidad",
            "Método",
            "Estado",
            "Fecha Vencimiento",
            "Fecha Pago",
            "Creado",
        ]
    )

    # `.iterator()` streams from the server-side cursor instead of materialising
    # the whole result as model objects before the first row is written.
    for payment in payments.iterator(chunk_size=2000):
        # csv_safe_row: names and concepts are free text set by any teacher, and
        # a leading =/+/-/@ makes the cell a formula in the admin's spreadsheet.
        writer.writerow(
            csv_safe_row(
                [
                    payment.id,
                    payment.student.full_name,
                    # Adult students have no parent — Payment.parent is nullable.
                    payment.parent.full_name if payment.parent else "",
                    payment.concept,
                    payment.amount,
                    payment.get_payment_method_display(),
                    payment.get_payment_status_display(),
                    payment.due_date.strftime("%d/%m/%Y") if payment.due_date else "",
                    (payment.payment_date.strftime("%d/%m/%Y") if payment.payment_date else ""),
                    payment.created_at.strftime("%d/%m/%Y %H:%M"),
                ]
            )
        )

    return response


@require_http_methods(["GET"])
@admin_required
def export_database_excel(request):
    """Export Estudiantes, Matrículas and Pagos as a single .xlsx file."""
    from billing.exports import build_database_workbook

    wb = build_database_workbook()
    today = datetime.now().strftime("%Y%m%d")
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="five_a_day_{today}.xlsx"'
    wb.save(response)
    return response


@require_http_methods(["GET"])
@admin_required
def search_parents(request):
    """AJAX endpoint to search parents"""
    query = request.GET.get("q", "").strip()

    if len(query) < 2:
        return JsonResponse({"results": []})

    parents = Parent.objects.filter(
        Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(email__icontains=query)
    )[:10]

    results = []
    for parent in parents:
        results.append(
            {
                "id": parent.id,
                "full_name": parent.full_name,
                "email": parent.email,
                "phone": parent.phone or "",
            }
        )

    return JsonResponse({"results": results})


@require_http_methods(["POST"])
@admin_required
def validate_student_parent(request):
    """AJAX endpoint to validate student-parent relationship"""
    try:
        data = json.loads(request.body)
        student_id = data.get("student_id")
        parent_id = data.get("parent_id")

        if not student_id:
            return JsonResponse({"valid": False, "message": "Missing student ID"})

        student = get_object_or_404(Student, id=student_id)

        # If parent_id is 0 or missing, return the student's parents list
        if not parent_id:
            parents_list = [{"id": p.id, "full_name": p.full_name, "email": p.email} for p in student.parents.all()]
            return JsonResponse({"valid": False, "parents": parents_list})

        get_object_or_404(Parent, id=parent_id)  # validates parent exists

        is_valid = student.parents.filter(id=parent_id).exists()

        response_data = {
            "valid": is_valid,
            "message": "Valid relationship" if is_valid else "Invalid relationship",
        }

        if is_valid:
            active_enrollment = student.enrollments.filter(status="active").first()
            if active_enrollment:
                response_data["enrollment"] = {
                    "id": active_enrollment.id,
                    "enrollment_type": active_enrollment.enrollment_type.display_name,
                    # Renamed with the model properties: the old `is_paid` /
                    # `remaining_amount` pair compared a year of collected money
                    # against the price of one period. Nothing consumes these —
                    # the create-payment form stopped calling this endpoint (see
                    # the note in payments.js) — so the shape is free to be
                    # honest rather than frozen.
                    "outstanding_amount": str(active_enrollment.outstanding_amount),
                    "overdue_amount": str(active_enrollment.overdue_amount),
                    "schedule_type": active_enrollment.get_schedule_type_display(),
                    "is_up_to_date": active_enrollment.is_up_to_date,
                }

        return JsonResponse(response_data)

    except json.JSONDecodeError:
        return JsonResponse({"valid": False, "message": "Invalid JSON data"}, status=400)
    except Http404:
        raise
    except Exception:
        logger.exception("Error validating payment payload")
        return JsonResponse({"valid": False, "message": "Datos de pago inválidos."}, status=400)
