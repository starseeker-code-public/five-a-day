import csv
import json
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Case, DecimalField, Q, Sum, Value, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from billing import constants
from billing.models import Payment
from core.constants import MESES_ES
from core.models import HistoryLog
from core.utils import csv_safe_row, safe_int
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

    Best-effort: a receipt that fails to send must never fail the request that
    recorded the money. Imported lazily to avoid a comms->billing import cycle.
    """
    try:
        from comms.tasks import send_payment_receipt_email_task

        send_payment_receipt_email_task.delay(int(payment_id))
    except Exception:  # noqa: BLE001 — receipt is nice-to-have
        logger.exception("Failed to enqueue payment receipt for payment %d", int(payment_id))


def _validated_choice(value, choices, default):
    """Return `value` when it is a valid choice key, otherwise `default`.

    `Model.objects.create()` does not enforce `choices`, so raw POST values
    reached the DB unchecked and `get_..._display()` then echoed them back.
    """
    return value if value in {key for key, _ in choices} else default


def _safe_int(raw, *, default, low=None, high=None):
    """Parse a query-string int, rejected to `default` outside [low, high].

    Thin alias for `core.utils.safe_int` — this helper existed here, in
    `app_forms` and in `reports` as three separate copies, and two other views
    were still missing it entirely.
    """
    return safe_int(raw, default=default, low=low, high=high)


def payments_list(request):
    """
    Main payments list view with pagination
    Shows active payments only (not deactivated ones)
    """
    # Get all active payments ordered by most recent first
    payments_queryset = Payment.objects.select_related(
        "student", "parent", "enrollment", "enrollment__enrollment_type"
    ).order_by("-due_date", "-created_at")

    # Add search functionality
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

    today = date.today()
    current_year = today.year

    # Month/year filter (server-side, so it narrows the whole dataset rather
    # than only the rows already rendered). Empty month = the whole year.
    selected_month = _safe_int(request.GET.get("month"), default=None, low=1, high=12)
    selected_year = _safe_int(request.GET.get("year"), default=current_year, low=2000, high=2100)
    payments_queryset = payments_queryset.filter(due_date__year=selected_year)
    if selected_month:
        payments_queryset = payments_queryset.filter(due_date__month=selected_month)

    # Years that actually have payments, so the dropdown never offers an
    # empty year. Always includes the current one.
    year_choices = sorted(
        {current_year} | {y for y in Payment.objects.values_list("due_date__year", flat=True).distinct() if y},
        reverse=True,
    )

    # Summary figures describe the SELECTED period, not "today", so the totals
    # always match the rows on screen. Previously "Esperado"/"Cobrado" were
    # hard-wired to the current month while "Pendiente"/"Vencido" were all-time
    # — three different periods in one line, which is a large part of why
    # "esperado" was reported as unintelligible.
    _zero = Decimal("0.00")
    period_payments = Payment.objects.filter(due_date__year=selected_year)
    if selected_month:
        period_payments = period_payments.filter(due_date__month=selected_month)

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
        except Exception:
            # Never echo str(e): on an IntegrityError it leaks the table and
            # column names, on a DataError the column width.
            logger.exception("Error creating payment")
            messages.error(request, "Error al crear el pago. Revisa los datos e inténtalo de nuevo.")
            return redirect("payments_list")

    return render(request, "payments/payment_create.html", {})


def payment_detail_view(request, payment_id):
    """
    Detailed view of a payment (read-only)
    """
    payment = get_object_or_404(Payment, id=payment_id)

    context = {
        "payment": payment,
    }

    return render(request, "payments/payment_detail.html", context)


@require_http_methods(["GET"])
def payment_receipt_pdf(request, payment_id):
    """Stream a payment-receipt PDF (v1.3)."""
    from billing.services.pdf_service import generate_payment_receipt

    payment = get_object_or_404(Payment.objects.select_related("student", "parent"), id=payment_id)
    pdf_bytes = generate_payment_receipt(payment)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="recibo-{payment.id}.pdf"'
    return response


@require_http_methods(["GET"])
def student_payments_pdf(request, student_id):
    """Stream a student's full payment history as a PDF.

    Shows the payment method and both dates per row, so the academy can see at
    a glance when each fee was paid and how. `?year=` narrows to one academic
    calendar year; omitted means every payment on record.
    """
    from billing.services.pdf_service import generate_student_payment_history

    student = get_object_or_404(Student.objects.select_related("group"), id=student_id)

    payments = Payment.objects.filter(student=student).select_related("enrollment").order_by("due_date", "id")

    year = _safe_int(request.GET.get("year"), default=None, low=2000, high=2100)
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
def update_payment(request, payment_id):
    """
    AJAX endpoint to update existing payment
    """
    try:
        payment = get_object_or_404(Payment, id=payment_id)
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
    except Exception:
        logger.exception("Error updating payment %d", int(payment_id))
        error_msg = "Error al actualizar el pago. Inténtalo de nuevo."
        if request.content_type == "application/json":
            return JsonResponse({"success": False, "error": error_msg}, status=500)
        messages.error(request, error_msg)
        return redirect("payments_list")


@require_http_methods(["POST"])
def delete_payment(request, payment_id):
    """
    AJAX endpoint to delete payment
    """
    try:
        payment = get_object_or_404(Payment, id=payment_id)
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
def deactivate_payment(request, payment_id):
    """
    Soft delete - deactivate payment instead of deleting
    """
    try:
        payment = get_object_or_404(Payment, id=payment_id)
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
def quick_complete_payment(request, payment_id):
    """
    AJAX endpoint to quickly complete a payment by setting its payment method.
    Expects JSON body: {"payment_method": "cash"|"transfer"|"credit_card"}
    """

    try:
        payment = get_object_or_404(Payment, id=payment_id)
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

        payment.payment_method = payment_method
        payment.payment_status = "completed"
        payment.payment_date = date.today()
        payment.save()

        HistoryLog.log(
            "payment_completed",
            f"Pago completado: {payment.student.full_name} — {payment.get_payment_method_display()} (€{payment.amount})",
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

    except Exception:
        logger.exception("Error completing payment %d", int(payment_id))
        return JsonResponse(
            {"success": False, "error": "Error al completar el pago."},
            status=500,
        )


@require_http_methods(["GET"])
def get_payment_details(request, payment_id):
    """
    AJAX endpoint to get payment details
    """
    try:
        payment = get_object_or_404(Payment, id=payment_id)

        return JsonResponse(
            {
                "success": True,
                "payment": {
                    "id": payment.id,
                    "student_id": payment.student.id,
                    "student_name": payment.student.full_name,
                    "parent_id": payment.parent.id if payment.parent else None,
                    "parent_name": payment.parent.full_name if payment.parent else "",
                    "enrollment_id": (payment.enrollment.id if payment.enrollment else None),
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


def payment_statistics(request):
    """
    Get payment statistics for dashboard
    """
    today = date.today()

    stats = {
        "total_payments": Payment.objects.count(),
        "completed_payments": Payment.objects.filter(payment_status="completed").count(),
        "pending_payments": Payment.objects.filter(payment_status="pending").count(),
        "overdue_payments": Payment.objects.filter(payment_status="pending", due_date__lt=today).count(),
        "total_amount_pending": Payment.objects.filter(payment_status="pending").aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00"),
        "total_amount_completed": Payment.objects.filter(payment_status="completed").aggregate(total=Sum("amount"))[
            "total"
        ]
        or Decimal("0.00"),
    }

    return JsonResponse(stats)


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


def export_payments(request):
    """
    Export payments to CSV
    """
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="pagos.csv"'

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

    payments = Payment.objects.all().select_related("student", "parent").order_by("-created_at")

    for payment in payments:
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


def export_database_excel(request):
    """Export Estudiantes, Matrículas and Pagos as a single .xlsx file."""
    from billing.exports import build_database_workbook

    wb = build_database_workbook()
    today = datetime.now().strftime("%Y%m%d")
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="five_a_day_{today}.xlsx"'
    wb.save(response)
    return response


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
    except Exception:
        logger.exception("Error validating payment payload")
        return JsonResponse({"valid": False, "message": "Datos de pago inválidos."}, status=400)
