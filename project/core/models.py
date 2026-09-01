"""
Core models — lightweight cross-cutting models that don't belong to a specific domain.
Domain models live in students/ and billing/.
"""

from datetime import date, timedelta

from django.db import models
from django.utils import timezone


class ScheduleSlot(models.Model):
    """Persists which group is assigned to each schedule slot (row, day, col)."""

    row = models.IntegerField()  # 0, 1, 2
    day = models.IntegerField()  # 0=Mon … 4=Fri
    col = models.IntegerField()  # 0 or 1
    group = models.ForeignKey(
        "students.Group", null=True, blank=True, on_delete=models.SET_NULL, related_name="schedule_slots"
    )

    class Meta:
        db_table = "schedule_slots"
        constraints = [
            models.UniqueConstraint(fields=["row", "day", "col"], name="unique_schedule_slot"),
        ]
        ordering = ["row", "day", "col"]

    def __str__(self):
        return f"Slot row={self.row} day={self.day} col={self.col}"


class FunFridayAttendance(models.Model):
    """Tracks which Fridays a student attended (or is registered for) Fun Friday."""

    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="fun_friday_dates")
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fun_friday_attendance"
        constraints = [
            models.UniqueConstraint(fields=["student", "date"], name="unique_fun_friday_attendance"),
        ]
        ordering = ["-date"]

    def __str__(self):
        return f"{self.student} - {self.date}"


class FunFridayScheduledSend(models.Model):
    """A Fun Friday announcement persisted until its scheduled send time.

    Replaces the old ``apply_async(eta=...)`` approach, which silently sends
    immediately under ``CELERY_TASK_ALWAYS_EAGER=True`` (production on Cloud
    Run has no Celery worker). Rows are drained by
    ``comms.tasks.send_due_fun_friday_emails_task`` — via Celery Beat in
    dev/testing and via the ``send_due_fun_friday_emails`` management command
    (Cloud Scheduler → Cloud Run Job) in production.
    """

    recipients = models.JSONField(default=list)  # list of email addresses
    day_name = models.CharField(max_length=20)
    day_number = models.PositiveSmallIntegerField()
    month = models.CharField(max_length=20)
    start_time = models.CharField(max_length=5)
    end_time = models.CharField(max_length=5)
    activity_description = models.TextField()
    minimum_age = models.PositiveSmallIntegerField(null=True, blank=True)
    maximum_age = models.PositiveSmallIntegerField(null=True, blank=True)
    meeting_point = models.CharField(max_length=255, null=True, blank=True)
    scheduled_for = models.DateTimeField(db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fun_friday_scheduled_sends"
        ordering = ["scheduled_for"]

    def __str__(self):
        status = "sent" if self.sent_at else "pending"
        return f"Fun Friday {self.day_name} {self.day_number} {self.month} ({status})"

    @property
    def is_due(self) -> bool:
        return self.sent_at is None and self.scheduled_for <= timezone.now()


class TodoItem(models.Model):
    text = models.CharField(max_length=500)
    due_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "todo_items"
        ordering = ["due_date", "created_at"]

    def __str__(self):
        return f"{self.text} ({self.due_date})"

    @property
    def is_overdue(self):
        return self.due_date < date.today()


# v1.10 — the immutable audit trail lives in a sibling module so this file
# stays focused on the user-visible core models. Re-exported here (and named in
# `__all__`) so `from core.models import AuditLog` keeps working.
from core.audit_models import AuditLog  # noqa: E402


class HistoryLog(models.Model):
    """Stores up to 1000 history log entries for user actions."""

    ACTION_CHOICES = [
        ("todo_completed", "Tarea completada"),
        ("payment_completed", "Pago completado"),
        ("student_enrolled", "Alumno matriculado"),
        ("teacher_created", "Profesor creado"),
        ("group_created", "Grupo creado"),
        ("group_updated", "Grupo actualizado"),
        ("config_updated", "Configuración actualizada"),
        ("payment_created", "Pago creado"),
        ("email_sent", "Email enviado"),
        # Used by the Fun Friday form, which queues the announcement rather
        # than sending it immediately. It was logged without being declared
        # here, so `get_action_display()` fell through to the raw slug and the
        # activity feed showed "email_scheduled" instead of Spanish.
        ("email_scheduled", "Email programado"),
        ("schedule_updated", "Horario actualizado"),
        # v1.1 — Waiting list & group capacity
        ("waiting_list_added", "Añadido a lista de espera"),
        ("waiting_list_assigned", "Asignado desde lista de espera"),
        ("waiting_list_spot_open", "Hueco disponible"),
        # v1.2 — Google Sheets integration
        ("sheets_exported", "Exportación a Google Sheets"),
    ]

    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    message = models.CharField(max_length=300)
    icon = models.CharField(max_length=40, default="history")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "history_logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"[{self.get_action_display()}] {self.message}"

    MAX_ENTRIES = 1000

    @classmethod
    def log(cls, action, message, icon="history"):
        """Create a history entry, enforcing the 1000-record cap."""
        entry = cls.objects.create(action=action, message=message, icon=icon)
        if cls.objects.count() > cls.MAX_ENTRIES:
            keep_ids = cls.objects.order_by("-created_at").values_list("id", flat=True)[: cls.MAX_ENTRIES]
            cls.objects.exclude(id__in=keep_ids).delete()
        return entry

    @classmethod
    def log_debounced(cls, action, message, icon="history", minutes=5):
        """Create a history entry only if no entry with the same action
        exists within the last `minutes` minutes."""
        cutoff = timezone.now() - timedelta(minutes=minutes)
        if cls.objects.filter(action=action, created_at__gte=cutoff).exists():
            return None
        return cls.log(action, message, icon=icon)


class Feature(models.Model):
    """A DESARROLLO — a Jira-style epic grouping the backlog tasks that deliver it.

    Deliberately lighter than :class:`BacklogTask`: no priority (an epic is
    scheduled by its ``deadline``, not ranked against its siblings) and no
    screenshot (it describes work to be done, not a defect that was seen). The
    tasks it spawns are ordinary backlog tasks and carry the priority.
    """

    STATUS_CHOICES = [
        ("open", "Abierto"),
        ("in_progress", "En progreso"),
        ("done", "Hecho"),
    ]

    title = models.CharField(max_length=300, verbose_name="Título")
    description = models.TextField(blank=True, verbose_name="Descripción")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="open", verbose_name="Estado")
    # Nullable and null by DEFAULT: a development is recorded long before anyone
    # commits to a date, and an invented deadline is worse than no deadline.
    deadline = models.DateField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Fecha límite",
        help_text="Opcional — fecha en la que debería estar terminado.",
    )
    created_by = models.CharField(max_length=100, default="anonymous", verbose_name="Creado por")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "features"
        ordering = ["-created_at"]
        verbose_name = "Desarrollo"
        verbose_name_plural = "Desarrollos"

    def __str__(self):
        return self.title

    @property
    def is_overdue(self):
        """True when the deadline has passed and the development is not done."""
        if not self.deadline or self.status == "done":
            return False
        return self.deadline < timezone.localdate()

    @property
    def days_left(self):
        """Days remaining until the deadline (negative when overdue), or None."""
        if not self.deadline:
            return None
        return (self.deadline - timezone.localdate()).days

    @property
    def task_count(self):
        return self.tasks.count()

    @property
    def done_task_count(self):
        return self.tasks.filter(status="done").count()

    @property
    def progress_percent(self):
        """Share of the linked tasks already done, 0–100 (0 when there are none)."""
        total = self.task_count
        if not total:
            return 0
        return round(self.done_task_count * 100 / total)


class BacklogTask(models.Model):
    """QA backlog tasks — created by testers, optionally emailed to support."""

    PRIORITY_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
    ]
    STATUS_CHOICES = [
        ("open", "Open"),
        ("in_progress", "In Progress"),
        ("done", "Done"),
    ]

    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="medium")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="open")
    created_by = models.CharField(max_length=100, default="anonymous")
    # The Desarrollo (epic) this task was broken out of, when it came from one.
    # SET_NULL: deleting the epic must never take the work items with it.
    feature = models.ForeignKey(
        "core.Feature",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
        verbose_name="Desarrollo",
    )
    # Set by the TESTER, not by a developer: a tick they turn green once they
    # have checked the fix on the testing environment. Deliberately separate
    # from `status="done"` — that is the developer saying "shipped" (and it
    # emails the admin teachers); this is QA saying "verified correct".
    verified = models.BooleanField(
        default=False,
        verbose_name="Verificado por QA",
        help_text="El tester ha comprobado que el ticket está correcto.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "backlog_tasks"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_priority_display()}] {self.title}"


class _SingletonQuerySet(models.QuerySet):
    """Blocks queryset-level deletes of a singleton row — see the twin in
    billing.models. `Model.objects.all().delete()` bypasses `Model.delete()`."""

    def delete(self):
        return (0, {})


class QAConfiguration(models.Model):
    """Singleton storing QA-specific toggles (error email reporting, etc.)."""

    objects = _SingletonQuerySet.as_manager()

    error_email_enabled = models.BooleanField(
        default=False,
        verbose_name="Send error reports via email",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qa_configuration"
        verbose_name = "Configuración QA"
        verbose_name_plural = "Configuración QA"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Returns Django's (count, per-model-counts) tuple; see SiteConfiguration."""
        return (0, {})

    @classmethod
    def get_config(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config


# Public model surface of this module, including the sibling-module
# re-export above.
__all__ = [
    "AuditLog",
    "BacklogTask",
    "FunFridayAttendance",
    "FunFridayScheduledSend",
    "HistoryLog",
    "QAConfiguration",
    "ScheduleSlot",
    "TodoItem",
]
