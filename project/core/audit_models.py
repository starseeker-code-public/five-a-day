"""
Audit log (v1.10).

Kept in its own module because HistoryLog (in core.models) is a compact
user-visible "recent activity" feed capped at 1,000 entries. The audit log
is the immutable machine-readable trail for who-changed-what-when. Different
retention, different consumers.
"""

from __future__ import annotations

import json
from typing import Any

from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """
    Immutable record of a data change.

    - `actor` is the auth.User who performed the change (None for system /
      Celery tasks / anonymous public flows).
    - `action` is one of create / update / delete.
    - `model` is the "app_label.ModelName" string, `object_id` is the PK.
    - `changes` is a JSON blob of {field: [old, new]} for updates,
      {field: value} for creates, or {} for deletes.
    """

    ACTION_CHOICES = [
        ("create", "Creación"),
        ("update", "Modificación"),
        ("delete", "Eliminación"),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    actor_label = models.CharField(
        max_length=200,
        blank=True,
        help_text="Human-readable actor tag, retained even if the user is later deleted.",
    )
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    model = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    object_label = models.CharField(max_length=300, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at"]
        verbose_name = "Registro de auditoría"
        verbose_name_plural = "Registros de auditoría"
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["model", "object_id"]),
            # No `Index(fields=["actor"])` — it is a ForeignKey and Django
            # indexes those by default. See the note on `Payment.Meta.indexes`.
        ]

    def __str__(self):
        return f"[{self.action}] {self.model}#{self.object_id} by {self.actor_label or 'system'}"

    @classmethod
    def record(
        cls,
        *,
        action: str,
        instance,
        changes: dict[str, Any] | None = None,
        actor=None,
        actor_label: str = "",
    ) -> AuditLog:
        """Convenience constructor used by signal receivers."""
        meta = instance._meta
        return cls.objects.create(
            actor=actor,
            actor_label=actor_label or (str(actor) if actor else ""),
            action=action,
            model=f"{meta.app_label}.{meta.object_name}",
            object_id=str(getattr(instance, "pk", "")),
            object_label=str(instance)[:300],
            changes=json.loads(json.dumps(changes or {}, default=str)),
        )
