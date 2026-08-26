from django.contrib import admin

from .models import (
    AuditLog,
    BacklogTask,
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
    list_display = ("action", "message", "icon", "created_at")
    list_filter = ("action",)
    ordering = ("-created_at",)
    readonly_fields = ("action", "message", "icon", "created_at")


@admin.register(TodoItem)
class TodoItemAdmin(admin.ModelAdmin):
    list_display = ("text", "due_date", "is_overdue", "created_at")
    list_filter = ("due_date",)
    ordering = ("due_date",)


@admin.register(ScheduleSlot)
class ScheduleSlotAdmin(admin.ModelAdmin):
    list_display = ("row", "day", "col", "group")
    list_filter = ("day", "group")
    ordering = ("row", "day", "col")


@admin.register(FunFridayAttendance)
class FunFridayAttendanceAdmin(admin.ModelAdmin):
    list_display = ("student", "date", "created_at")
    list_filter = ("date",)
    ordering = ("-date",)
    raw_id_fields = ("student",)


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
    list_display = ("title", "priority", "status", "created_by", "created_at")
    list_filter = ("status", "priority")
    search_fields = ("title", "description")
    ordering = ("-created_at",)
