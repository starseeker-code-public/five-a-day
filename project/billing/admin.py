import csv
from datetime import date
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.urls import reverse
from django.utils.html import format_html

from billing.constants import PERIODIC_PAYMENT_TYPES
from billing.models import (
    UNCOLLECTED_PAYMENT_STATUSES,
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
        ("Titularidad", {"fields": ("student", "parent", "enrollment")}),
        (
            "Detalles del pago",
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
        ("Fechas", {"fields": ("due_date", "payment_date")}),
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
            status_text += f" ({obj.days_overdue} d de retraso)"

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

        # ONLY pending rows. `exclude("completed")` also caught `cancelled`,
        # `failed` and `refunded` — so this action resurrected them, dated them
        # today (re-booking the money as this month's income) and emailed a
        # receipt for a refund. Cancelling frees the month under the pending-only
        # unique index, so a cancelled duplicate completed here would double-bill
        # with nothing to stop it — exactly what `quick_complete_payment` refuses.
        pending = list(queryset.filter(payment_status="pending"))
        today = date.today()
        for payment in pending:
            payment.payment_status = "completed"
            payment.payment_date = today
            payment.save(update_fields=["payment_status", "payment_date", "updated_at"])
            _queue_payment_receipt(payment.id)

        skipped = queryset.count() - len(pending)
        message = f"{len(pending)} pagos marcados como completados."
        if skipped:
            message += f" {skipped} no pendientes (completados/cancelados/fallidos) sin tocar."
        self.message_user(request, message)

    mark_as_completed.short_description = "Marcar como completados"

    def _reopen_to_pending(self, request, queryset):
        """Set the selection back to `pending`, skipping rows that cannot be.

        Reopening is the one status change the database can refuse:
        `unique_pending_periodic_payment_per_month` allows a single PENDING
        monthly/quarterly row per student per due-month, and cancelling frees
        that month so the schedule may already have re-billed it. Restoring the
        cancelled row then collides with its own replacement — which is not an
        edge case but the normal aftermath of the documented
        `reconcile_payment_schedule` repair.

        A bare `queryset.update(...)` lost that argument twice over: it raised a
        raw `IntegrityError` (an unhandled 500, never the constraint's Spanish
        `violation_error_message`, because `update()` does not call
        `full_clean()`), and the failure rolled back the whole action, so none
        of the selection was reopened. Colliding rows are now reported by name
        and the rest go through.

        Row-by-row `save()` rather than `update()` for the same reason
        `mark_as_completed` loops: `Payment` is audit-tracked, and `update()`
        bypasses the `pre_save`/`post_save` receivers, so a bulk status change
        left no `AuditLog` entry and did not bump `updated_at`.
        """
        rows = list(queryset)
        selected_ids = {p.id for p in rows}

        # The months already held by a pending periodic row that is NOT part of
        # this selection. Resolved in one query rather than one per row.
        occupied = set()
        periodic = [p for p in rows if p.payment_type in PERIODIC_PAYMENT_TYPES]
        if periodic:
            occupied = {
                (student_id, payment_type, due_date.year, due_date.month)
                for student_id, payment_type, due_date in Payment.objects.filter(
                    student_id__in={p.student_id for p in periodic},
                    payment_type__in=PERIODIC_PAYMENT_TYPES,
                    payment_status="pending",
                )
                .exclude(id__in=selected_ids)
                .values_list("student_id", "payment_type", "due_date")
            }

        reopened = 0
        blocked = []
        for payment in rows:
            slot = None
            if payment.payment_type in PERIODIC_PAYMENT_TYPES and payment.due_date is not None:
                slot = (payment.student_id, payment.payment_type, payment.due_date.year, payment.due_date.month)
                if slot in occupied:
                    blocked.append(payment)
                    continue

            payment.payment_status = "pending"
            payment.payment_date = None
            try:
                # Savepoint per row: a genuine race (something else claiming the
                # month between the query above and this write) must not undo
                # the rows already reopened.
                with transaction.atomic():
                    payment.save(update_fields=["payment_status", "payment_date", "updated_at"])
            except IntegrityError:
                blocked.append(payment)
                continue

            if slot is not None:
                # Claim it, so two selected rows falling in the same month do
                # not collide with each other either.
                occupied.add(slot)
            reopened += 1

        message = f"{reopened} pagos reabiertos como pendientes."
        if blocked:
            names = ", ".join(f"{p.student} ({p.concept})" for p in blocked[:5])
            if len(blocked) > 5:
                names += f" y {len(blocked) - 5} más"
            message += (
                f" {len(blocked)} sin reabrir: ya existe un pago pendiente de ese tipo "
                f"para ese alumno en ese mes — {names}."
            )
        self.message_user(request, message, level=messages.WARNING if blocked else messages.INFO)

    def mark_as_pending(self, request, queryset):
        """Reopen the selection. Clears `payment_date` — a pending payment has
        not been collected, and every income figure filters on that date, so
        leaving it set reported money against a payment nobody has paid."""
        self._reopen_to_pending(request, queryset)

    mark_as_pending.short_description = "Marcar como pendientes"

    #: Statuses `_bulk_set_status` refuses to move COLLECTED money into.
    _VOIDING_STATUSES = ("failed", "cancelled")

    def _bulk_set_status(self, queryset, status: str):
        """Set `payment_status` row by row so the audit signals fire.

        Returns `(changed, protected)` — the rows moved, and the COMPLETED rows
        left alone.

        `queryset.update()` fires NEITHER the pre/post_save receivers (Payment is
        audit-tracked, so a bulk status change left no AuditLog row for money
        being voided) NOR `auto_now` on `updated_at` (the row kept its original
        timestamp). Voiding billed money with no trace is exactly what the audit
        log exists to prevent — and `_reopen_to_pending` already loops for the
        same reason.

        A COMPLETED payment is never moved to `failed` or `cancelled`. That money
        has been collected and is counted as income in a month that is very likely
        already closed and reported; voiding it silently subtracts it from those
        figures, and neither status describes what happened (it did not fail, and
        it was not withdrawn from the schedule). `mark_as_completed` already skips
        every non-pending row for the mirror-image reason. Reopening it first
        ("Marcar como pendientes") is the auditable route, and that action has to
        pass the pending-per-month constraint, which is exactly the check a
        re-collection needs.
        """
        changed = 0
        protected = []
        for payment in queryset:
            if payment.payment_status == status:
                continue
            if payment.payment_status == "completed" and status in self._VOIDING_STATUSES:
                protected.append(payment)
                continue
            payment.payment_status = status
            payment.save(update_fields=["payment_status", "updated_at"])
            changed += 1
        return changed, protected

    def _report_bulk_status(self, request, changed: int, protected: list, done: str) -> None:
        message = f"{changed} pagos marcados como {done}."
        if protected:
            names = ", ".join(f"{p.student} ({p.concept})" for p in protected[:5])
            if len(protected) > 5:
                names += f" y {len(protected) - 5} más"
            message += (
                f" {len(protected)} sin tocar: están completados, es decir cobrados, "
                f"y anularlos restaría ese dinero de los ingresos ya declarados. "
                f"Reábrelos con «Marcar como pendientes» si hay que corregirlos — {names}."
            )
        self.message_user(request, message, level=messages.WARNING if protected else messages.INFO)

    def mark_as_failed(self, request, queryset):
        changed, protected = self._bulk_set_status(queryset, "failed")
        self._report_bulk_status(request, changed, protected, "fallidos")

    mark_as_failed.short_description = "Marcar como fallidos"

    def soft_delete_payments(self, request, queryset):
        changed, protected = self._bulk_set_status(queryset, "cancelled")
        self._report_bulk_status(request, changed, protected, "cancelados (borrado lógico)")

    soft_delete_payments.short_description = "Cancelar los pagos seleccionados"

    def restore_payments(self, request, queryset):
        # Same operation as mark_as_pending, including the collision handling:
        # restoring a cancelled row is precisely when the month is most likely
        # to have been re-billed already.
        self._reopen_to_pending(request, queryset)

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
    list_display = ["__str__", "academy_cif", "full_time_monthly_fee", "part_time_monthly_fee", "updated_at"]
    # No `fieldsets` on purpose: a field absent from a fieldset is UNREACHABLE,
    # not merely hidden, and this is the one row that holds every live price. The
    # `academy_*` columns therefore appear here the moment they exist. `academy_cif`
    # is surfaced in `list_display` because it is the field that decides whether a
    # tax certificate is usable, and it ships empty.

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
    # NOT the `is_up_to_date` / `overdue_amount` / `outstanding_amount` model
    # properties: each of the three calls `payment_totals()`, so rendering the
    # change form ran the same aggregate query three times. These display methods
    # share one result per object (`_totals`). The properties themselves are left
    # alone — they are read all over the app on instances the caller expects to be
    # live, and memoising them on the model would serve a stale figure to anyone
    # who created a payment and re-read the same instance.
    readonly_fields = [
        "created_at",
        "updated_at",
        "up_to_date_display",
        "overdue_display",
        "outstanding_display",
    ]
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
                # Same status set `Enrollment.payment_totals()` counts, read from
                # the one constant so the annotation and the method cannot drift
                # (this is an optimisation OF that method, and a silent
                # disagreement between them shows the admin one debt figure while
                # every other page shows another). Deliberately not
                # `LIVE_PAYMENT_STATUSES` — that includes `completed`, which is
                # money already banked, not money outstanding.
                _outstanding=Coalesce(
                    Sum(
                        "payments__amount",
                        filter=Q(payments__payment_status__in=UNCOLLECTED_PAYMENT_STATUSES),
                        output_field=money,
                    ),
                    zero,
                ),
                _overdue=Coalesce(
                    Sum(
                        "payments__amount",
                        filter=Q(
                            payments__payment_status__in=UNCOLLECTED_PAYMENT_STATUSES,
                            payments__due_date__lt=today,
                        ),
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
        ("Información adicional", {"fields": ("document_url", "notes"), "classes": ("collapse",)}),
        (
            "Sistema",
            {
                "fields": (
                    "up_to_date_display",
                    "overdue_display",
                    "outstanding_display",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    @staticmethod
    def _totals(obj) -> EnrollmentPaymentTotals:
        """One `payment_totals()` per object per request.

        `get_queryset` annotates the three figures for the changelist; the change
        form renders a plain instance, so it falls back to the model method — and
        three readonly fields asking three separate questions meant three
        identical aggregate queries. Cached on the instance, which lives exactly
        as long as the response that is rendering it.
        """
        if hasattr(obj, "_overdue"):
            return EnrollmentPaymentTotals(overdue=obj._overdue, outstanding=obj._outstanding, billed=obj._billed)
        cached = getattr(obj, "_admin_totals", None)
        if cached is None:
            cached = obj.payment_totals()
            obj._admin_totals = cached
        return cached

    @admin.display(description="Al día", boolean=True)
    def up_to_date_display(self, obj):
        return self._totals(obj).overdue == Decimal("0.00")

    @admin.display(description="Vencido")
    def overdue_display(self, obj):
        return f"{self._totals(obj).overdue:.2f} €"

    @admin.display(description="Pendiente de cobro")
    def outstanding_display(self, obj):
        return f"{self._totals(obj).outstanding:.2f} €"

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
        # Annotated by get_queryset on the changelist; computed on demand (and
        # memoised) for a single instance (the change form, a shell, a test).
        totals = self._totals(obj)
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


class ExpenseAdminForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        # Run the model's own per-frequency rules (including the recurring_weekdays
        # parse) so the admin cannot save a template the app path would reject.
        # Without this, typing "L,M,V" into recurring_weekdays saved a row whose
        # `weekday_set()` then raised ValueError — a 500 on /expenses/ for
        # everyone and an aborted daily materialiser for all weekly/yearly rows.
        instance = self.instance
        for field, value in cleaned.items():
            setattr(instance, field, value)
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return cleaned


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    form = ExpenseAdminForm
    list_display = ["expense_date", "description", "category", "amount", "is_recurring", "recurring_summary"]
    list_filter = ["category", "is_recurring", "expense_date"]
    search_fields = ["description", "notes"]
    readonly_fields = ["created_at", "updated_at", "generated_from"]

    @admin.display(description="Recurrencia")
    def recurring_summary(self, obj):
        return obj.recurring_summary() or "—"
