import csv
from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.urls import reverse
from django.utils.html import format_html

from billing.models import (
    Enrollment,
    EnrollmentPaymentTotals,
    EnrollmentType,
    Expense,
    Payment,
    SiteConfiguration,
)
from core.utils import csv_safe_row


@admin.register(EnrollmentType)
class EnrollmentTypeAdmin(admin.ModelAdmin):
    """The four matrícula categories. Reference data, not user content.

    `EnrollmentService._resolve_enrollment_type` looks these up BY NAME and
    raises `ValueError: EnrollmentType '<name>' not found` when one is missing —
    and because `Enrollment.enrollment_type` is a non-null PROTECT FK, that
    blocks every enrollment of every kind. A row that is not yet referenced by
    any enrollment has nothing protecting it, so deleting it from here was one
    click away from an academy that cannot enrol anybody. Production shipped
    with this table empty once already (v1.17.1).

    `name` is the lookup key, so it is read-only once the row exists: the
    field's `choices` stop you inventing a new value, but not swapping `special`
    onto the row the code resolves as `adults`.

    `base_amount_*` is the ONE-TIME matrícula fee for the category, not a
    monthly cuota — it mirrors what an admin edits in `/management/`, so it is
    shown here to make drift visible. `seed_enrollment_types` re-derives it.
    """

    list_display = ["name", "display_name", "base_amount_full_time", "base_amount_part_time", "active"]
    list_filter = ["active"]
    search_fields = ["name", "display_name"]

    def get_readonly_fields(self, request, obj=None):
        return ["name"] if obj else []

    def has_delete_permission(self, request, obj=None):
        from billing.services.enrollment_type_service import REQUIRED_ENROLLMENT_TYPES

        if obj is not None and obj.name in REQUIRED_ENROLLMENT_TYPES:
            return False
        return super().has_delete_permission(request, obj)


# Payments and enrollments
@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "student_link",
        "parent_link",
        "concept",
        "amount_display",
        "payment_method",
        "status_display",
        "due_date",
        "payment_date",
        "is_overdue_display",
    ]
    list_filter = [
        "payment_status",
        "payment_method",
        "payment_type",
        "currency",
        "due_date",
        "payment_date",
        "created_at",
    ]
    search_fields = [
        "student__first_name",
        "student__last_name",
        "parent__first_name",
        "parent__last_name",
        "concept",
        "reference_number",
    ]
    readonly_fields = ["created_at", "updated_at", "is_overdue", "days_overdue"]
    raw_id_fields = ["student", "parent", "enrollment"]

    fieldsets = (
        ("Payment Information", {"fields": ("student", "parent", "enrollment")}),
        (
            "Payment Details",
            {
                "fields": (
                    "payment_type",
                    "payment_method",
                    "amount",
                    "currency",
                    "payment_status",
                    "concept",
                    "reference_number",
                )
            },
        ),
        ("Dates", {"fields": ("due_date", "payment_date")}),
        ("Información adicional", {"fields": ("observations", "document_url"), "classes": ("collapse",)}),
        (
            "Sistema",
            {"fields": ("created_at", "updated_at", "is_overdue", "days_overdue"), "classes": ("collapse",)},
        ),
    )

    actions = [
        "mark_as_completed",
        "mark_as_pending",
        "mark_as_failed",
        "soft_delete_payments",
        "restore_payments",
        "export_to_csv",
    ]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("student", "parent", "enrollment", "enrollment__enrollment_type")
        )

    def student_link(self, obj):
        if obj.student:
            try:
                app_label = obj.student._meta.app_label
                model_name = obj.student._meta.model_name
                url = reverse(f"admin:{app_label}_{model_name}_change", args=[obj.student.id])
                return format_html('<a href="{}">{}</a>', url, obj.student.full_name)
            except Exception:
                return obj.student.full_name
        return "-"

    student_link.short_description = "Alumno"
    student_link.admin_order_field = "student__last_name"

    def parent_link(self, obj):
        if obj.parent:
            try:
                app_label = obj.parent._meta.app_label
                model_name = obj.parent._meta.model_name
                url = reverse(f"admin:{app_label}_{model_name}_change", args=[obj.parent.id])
                return format_html('<a href="{}">{}</a>', url, obj.parent.full_name)
            except Exception:
                return obj.parent.full_name

        return "-"

    parent_link.short_description = "Padre/Tutor"
    parent_link.admin_order_field = "parent__last_name"

    def amount_display(self, obj):
        return format_html("<span>&euro;{} <small>({})</small></span>", obj.amount, obj.currency)

    amount_display.short_description = "Importe"
    amount_display.admin_order_field = "amount"

    def status_display(self, obj):
        colors = {
            "completed": "green",
            "pending": "orange" if not obj.is_overdue else "red",
            "failed": "red",
            "cancelled": "gray",
            "refunded": "blue",
        }
        color = colors.get(obj.payment_status, "gray")

        status_text = obj.get_payment_status_display()
        if obj.is_overdue and obj.payment_status == "pending":
            status_text += f" ({obj.days_overdue}d overdue)"

        return format_html('<span style="color: {}; font-weight: bold;">{}</span>', color, status_text)

    status_display.short_description = "Estado"
    status_display.admin_order_field = "payment_status"

    def is_overdue_display(self, obj):
        return obj.is_overdue

    is_overdue_display.short_description = "Vencido"
    is_overdue_display.boolean = True

    # Admin actions
    def mark_as_completed(self, request, queryset):
        """Complete the PENDING payments in the selection, and email each receipt.

        Deliberately not a bare `queryset.update(...)`. That version had the two
        bugs the UI path already guards against:

        * it rewrote `payment_date` on rows that were ALREADY completed, moving
          historical money into the current month in every income report — the
          same regression `quick_complete_payment` short-circuits on;
        * it sent no receipt, so cash and transfer payments completed from the
          admin were silently unacknowledged (see `_queue_payment_receipt`).

        Imported lazily: `core.views.payments` pulls in billing models, so a
        module-level import here would close the loop at load time.
        """
        from core.views.payments import _queue_payment_receipt

        pending = list(queryset.exclude(payment_status="completed"))
        today = date.today()
        for payment in pending:
            payment.payment_status = "completed"
            payment.payment_date = today
            payment.save(update_fields=["payment_status", "payment_date", "updated_at"])
            _queue_payment_receipt(payment.id)

        skipped = queryset.count() - len(pending)
        message = f"{len(pending)} payments marked as completed."
        if skipped:
            message += f" {skipped} already completed and left untouched."
        self.message_user(request, message)

    mark_as_completed.short_description = "Marcar como completados"

    def mark_as_pending(self, request, queryset):
        """Reopen the selection. Clears `payment_date` — a pending payment has
        not been collected, and every income figure filters on that date, so
        leaving it set reported money against a payment nobody has paid."""
        updated = queryset.update(payment_status="pending", payment_date=None)
        self.message_user(request, f"{updated} payments marked as pending.")

    mark_as_pending.short_description = "Marcar como pendientes"

    def mark_as_failed(self, request, queryset):
        updated = queryset.update(payment_status="failed")
        self.message_user(request, f"{updated} payments marked as failed.")

    mark_as_failed.short_description = "Marcar como fallidos"

    def soft_delete_payments(self, request, queryset):
        updated = queryset.update(payment_status="cancelled")
        self.message_user(request, f"{updated} payments cancelled (soft deleted).")

    soft_delete_payments.short_description = "Cancelar los pagos seleccionados"

    def restore_payments(self, request, queryset):
        # payment_date cleared for the same reason as mark_as_pending.
        updated = queryset.update(payment_status="pending", payment_date=None)
        self.message_user(request, f"{updated} payments restored to pending.")

    restore_payments.short_description = "Restaurar a pendiente"

    def export_to_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="payments.csv"'

        writer = csv.writer(response)
        writer.writerow(
            [
                "ID",
                "Student",
                "Parent",
                "Concept",
                "Amount",
                "Currency",
                "Payment Method",
                "Status",
                "Due Date",
                "Payment Date",
                "Reference",
                "Created",
            ]
        )

        for payment in queryset:
            # csv_safe_row: see core.utils — names/concepts are free text and a
            # leading =/+/-/@ turns the cell into a spreadsheet formula.
            writer.writerow(
                csv_safe_row(
                    [
                        payment.id,
                        payment.student.full_name if payment.student else "",
                        payment.parent.full_name if payment.parent else "",
                        payment.concept,
                        payment.amount,
                        payment.currency,
                        payment.get_payment_method_display(),
                        payment.get_payment_status_display(),
                        payment.due_date.strftime("%Y-%m-%d") if payment.due_date else "",
                        payment.payment_date.strftime("%Y-%m-%d") if payment.payment_date else "",
                        payment.reference_number,
                        payment.created_at.strftime("%Y-%m-%d %H:%M"),
                    ]
                )
            )

        return response

    export_to_csv.short_description = "Exportar a CSV"


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    list_display = ["__str__", "full_time_monthly_fee", "part_time_monthly_fee", "updated_at"]

    def has_add_permission(self, request):
        return not SiteConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = [
        "student",
        "enrollment_type",
        "schedule_type",
        "status",
        "enrollment_period_start",
        "enrollment_period_end",
        "final_amount",
        "payment_status_display",
    ]
    list_filter = ["status", "schedule_type", "enrollment_type", "enrollment_period_start", "enrollment_date"]
    search_fields = ["student__first_name", "student__last_name", "notes"]
    readonly_fields = ["created_at", "updated_at", "is_up_to_date", "overdue_amount", "outstanding_amount"]
    raw_id_fields = ["student"]

    def get_queryset(self, request):
        """Resolve the payment-status column in the LIST query, not per row.

        `payment_status_display` calls `Enrollment.payment_totals()`, which is one
        query per enrollment — so the changelist cost 101 queries for a 100-row
        page. It cannot be fixed with `prefetch_related` either: `payment_totals`
        reads `self.payments.values_list(...)`, and a `values_list()` on a related
        manager builds a fresh queryset that ignores the prefetch cache.

        Annotating gets the same three figures in the single changelist query. The
        display method still falls back to `payment_totals()` when the annotations
        are absent, so the change form (which renders `overdue_amount` /
        `outstanding_amount` as readonly fields on a plain instance) is unaffected.
        """
        today = date.today()
        money = DecimalField(max_digits=12, decimal_places=2)
        zero = Value(Decimal("0.00"), output_field=money)
        return (
            super()
            .get_queryset(request)
            .select_related("student", "enrollment_type")
            .annotate(
                _billed=Count("payments", distinct=True),
                _outstanding=Coalesce(
                    Sum("payments__amount", filter=Q(payments__payment_status="pending"), output_field=money),
                    zero,
                ),
                _overdue=Coalesce(
                    Sum(
                        "payments__amount",
                        filter=Q(payments__payment_status="pending", payments__due_date__lt=today),
                        output_field=money,
                    ),
                    zero,
                ),
            )
        )

    fieldsets = (
        ("Alumno", {"fields": ("student",)}),
        (
            "Detalles de la matrícula",
            {
                "fields": (
                    "enrollment_type",
                    "schedule_type",
                    "status",
                    "enrollment_period_start",
                    "enrollment_period_end",
                    "enrollment_date",
                )
            },
        ),
        # `payment_modality`, the two discount flags and `academic_year` were
        # missing from every fieldset, so none of them could be seen or
        # corrected here. They are not cosmetic: the modality decides monthly vs
        # quarterly billing, both flags feed `calculate_period_amount`, and
        # `academic_year` is the field `generate_payments` filters on — an
        # enrollment stamped with the wrong year is simply never billed, and
        # there was nowhere in the app to fix it.
        (
            "Plan de cobro",
            {
                "fields": ("academic_year", "payment_modality", "is_sibling_discount", "has_language_cheque"),
                "description": (
                    "«Año académico» es el campo por el que <code>generate_payments</code> "
                    "selecciona las matrículas: si está mal, no se generan cobros."
                ),
            },
        ),
        ("Precios", {"fields": ("enrollment_amount", "discount_percentage", "final_amount")}),
        ("Additional Information", {"fields": ("document_url", "notes"), "classes": ("collapse",)}),
        (
            "System Information",
            {
                "fields": (
                    "is_up_to_date",
                    "overdue_amount",
                    "outstanding_amount",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def payment_status_display(self, obj):
        """Does this family owe money, and is any of it late?

        Four states rather than the old paid/unpaid pair, because "nothing
        overdue" and "nothing outstanding" are different facts and the admin
        needs both: a family who has paid everything due but whose current month
        is still open is up to date, not in arrears, and must not appear on the
        chase list beside someone three months behind.

        EVERY branch must pass at least one argument to format_html(): on Django
        6.0+ a call with only a format string raises TypeError (it was a
        RemovedInDjango60Warning before), and the branch that had none 500'd the
        whole changelist as soon as one enrollment reached it.
        """
        # Annotated by get_queryset on the changelist; computed on demand for a
        # single instance (the change form, a shell, a test).
        if hasattr(obj, "_overdue"):
            totals = EnrollmentPaymentTotals(overdue=obj._overdue, outstanding=obj._outstanding, billed=obj._billed)
        else:
            totals = obj.payment_totals()
        if totals.overdue:
            return format_html('<span style="color: red;">&#10007; Vencido (&euro;{})</span>', totals.overdue)
        if totals.outstanding:
            return format_html(
                '<span style="color: #b45309;">&#8226; Al día ({} &euro;{} aún no vencidos)</span>',
                "still",
                totals.outstanding,
            )
        if not totals.billed:
            return format_html('<span style="color: gray;">{}</span>', "Sin cobros emitidos")
        return format_html('<span style="color: green;">&#10003; {}</span>', "Al corriente")

    payment_status_display.short_description = "Estado de pago"


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ["expense_date", "description", "category", "amount", "is_recurring"]
    list_filter = ["category", "is_recurring", "expense_date"]
    search_fields = ["description", "notes"]
    readonly_fields = ["created_at", "updated_at", "generated_from"]
