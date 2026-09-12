from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError

from core.schedule_utils import DAY_NAMES_ES, is_valid_slot, slot_time_range

from .models import (
    AuditLog,
    BacklogTask,
    Feature,
    FunFridayAttendance,
    FunFridayScheduledSend,
    HistoryLog,
    ScheduleSlot,
    TodoItem,
)

admin.site.site_header = "Five a Day eVolution"
admin.site.site_title = "Five a Day · Admin"
admin.site.index_title = "Panel de administración"


@admin.register(HistoryLog)
class HistoryLogAdmin(admin.ModelAdmin):
    """The activity feed. Written by `HistoryLog.log()`, never by hand.

    Every field is read-only, but Add was still offered — so the add form
    rendered with no inputs at all and saving it created a row with
    `action=""` and `message=""`. That is not merely untidy: the feed is capped
    at 1,000 rows, so each blank row evicts a real one, and `action=""` matches
    no choice, making `get_action_display()` render empty in the dropdown.
    """

    list_display = ("action", "message", "icon", "created_at")
    list_filter = ("action",)
    search_fields = ("message",)
    ordering = ("-created_at",)
    readonly_fields = ("action", "message", "icon", "created_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Add and change were blocked but delete was not, so the whole feed was
        # one `delete_selected` from erasure. The feed self-manages via its
        # 1,000-row cap; it should not be hand-deletable either.
        return False


@admin.register(TodoItem)
class TodoItemAdmin(admin.ModelAdmin):
    list_display = ("text", "due_date", "is_overdue", "created_at")
    list_filter = ("due_date",)
    ordering = ("due_date",)


class ScheduleSlotAdminForm(forms.ModelForm):
    """Applies the grid validator the API uses, so the two cannot disagree."""

    class Meta:
        model = ScheduleSlot
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        row, day, col = cleaned.get("row"), cleaned.get("day"), cleaned.get("col")
        if None not in (row, day, col) and not is_valid_slot(row, day, col):
            raise ValidationError(
                "Esa celda no existe en el cuadrante semanal "
                "(el viernes solo tiene las filas 0 y 1). Revisa fila, día y columna."
            )
        return cleaned


@admin.register(ScheduleSlot)
class ScheduleSlotAdmin(admin.ModelAdmin):
    """Cells of the weekly grid.

    `save_schedule_slot` validates (row, day, col) against the real grid via
    `is_valid_slot` — Friday only runs rows 0 and 1, and its cells are keyed on
    (row, col) rather than sharing a band. The admin had no such check, so it
    could write a slot the rest of the app considers impossible: row 2 on a
    Friday, or row 99. `slot_time_range` degrades to a "--:--" placeholder and
    `schedule_view` skips unknown rows, so it no longer takes the page down, but
    the row is still junk that renders as a blank cell nobody can explain.

    The same validator is applied here rather than duplicated.
    """

    list_display = ("row", "day", "day_label", "col", "group", "time_range")
    list_filter = ("day", "group")
    ordering = ("row", "day", "col")
    list_select_related = ("group",)

    @admin.display(description="Día")
    def day_label(self, obj):
        if 0 <= obj.day < len(DAY_NAMES_ES):
            return DAY_NAMES_ES[obj.day]
        return f"⚠ {obj.day}"

    @admin.display(description="Horario")
    def time_range(self, obj):
        start, end = slot_time_range(obj.row, obj.day, obj.col)
        if not is_valid_slot(obj.row, obj.day, obj.col):
            return f"⚠ fuera del cuadrante ({start}–{end})"
        return f"{start}–{end}"

    form = ScheduleSlotAdminForm


@admin.register(FunFridayAttendance)
class FunFridayAttendanceAdmin(admin.ModelAdmin):
    list_display = ("student", "date", "created_at")
    list_filter = ("date",)
    ordering = ("-date",)
    raw_id_fields = ("student",)
    # Explicit rather than load-bearing: `ChangeList.apply_select_related` already
    # auto-joins any PLAIN FK named in `list_display`, so this changelist was never
    # N+1 (measured: 12 queries either way). Declaring it keeps that true if
    # `student` is ever wrapped in a display callable — the auto-detection only
    # sees bare field names, which is exactly how
    # `EnrollmentAdmin.payment_status_display` slipped past it.
    list_select_related = ("student",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only window onto the audit trail.

    The trail was previously write-only: nothing registered it, no view
    rendered it and no URL reached it, so the rows it accumulated could never
    actually be inspected. It stays immutable here — add/change are disabled
    and every field is read-only — but it is at last readable.
    """

    list_display = ("created_at", "action", "model", "object_id", "object_label", "actor_label")
    list_filter = ("action", "model", "created_at")
    search_fields = ("object_label", "actor_label", "object_id")
    ordering = ("-created_at",)
    readonly_fields = ("actor", "actor_label", "action", "model", "object_id", "object_label", "changes", "created_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        """An audit trail an admin can erase is not an audit trail.

        Add and change were blocked and the docstring above claimed the model
        was immutable, but delete was never overridden — so both the per-row
        "Delete" button and the bulk `delete_selected` action worked, and the
        record of who changed what could be removed by the very account it
        incriminates. Ageing rows out is `core.tasks.prune_audit_log` (two-year
        horizon), not a person with a mouse.
        """
        return False


@admin.register(FunFridayScheduledSend)
class FunFridayScheduledSendAdmin(admin.ModelAdmin):
    """Queued Fun Friday announcements.

    Registered so a scheduled mass-mail can be inspected — or cancelled — before
    it goes out to every parent. There was previously no way to see one.
    """

    list_display = ("day_name", "day_number", "month", "scheduled_for", "sent_at", "recipient_count")
    list_filter = ("sent_at",)
    ordering = ("-scheduled_for",)
    readonly_fields = ("sent_at", "created_at", "updated_at")

    @admin.display(description="Destinatarios")
    def recipient_count(self, obj):
        return len(obj.recipients or [])


@admin.register(BacklogTask)
class BacklogTaskAdmin(admin.ModelAdmin):
    list_display = ("title", "priority", "status", "feature", "created_by", "created_at")
    list_filter = ("status", "priority", "feature")
    search_fields = ("title", "description")
    ordering = ("-created_at",)
    # Explicit, not load-bearing — see the note on FunFridayAttendanceAdmin.
    list_select_related = ("feature",)


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "deadline", "overdue_display", "created_by", "created_at")
    list_filter = ("status", "deadline")
    search_fields = ("title", "description")
    ordering = ("-created_at",)

    @admin.display(boolean=True, description="Vencido")
    def overdue_display(self, obj):
        return obj.is_overdue
