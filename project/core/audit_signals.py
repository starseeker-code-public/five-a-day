"""
Signal receivers that populate the AuditLog (v1.10).

Registered in `core.apps.CoreConfig.ready()` so the receivers are loaded
exactly once, without polluting model import time.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

# Actor propagated from the middleware. `contextvars.ContextVar` is
# request-local under WSGI + async-safe under ASGI — safer than threadlocals.
_current_actor: contextvars.ContextVar = contextvars.ContextVar("audit_actor", default=None)

# Models we track. Kept as a small explicit list so we don't accidentally
# log every schema change or bookkeeping row (HistoryLog, AuditLog itself,
# ParentSessionToken, sessions…).
_TRACKED = (
    ("students", "Student"),
    ("students", "Parent"),
    ("students", "Teacher"),
    ("students", "Group"),
    ("billing", "Enrollment"),
    ("billing", "Payment"),
    ("billing", "SiteConfiguration"),
    ("billing", "Expense"),
)


def set_current_actor(user):
    _current_actor.set(user)


def _is_tracked(sender) -> bool:
    meta = getattr(sender, "_meta", None)
    if meta is None:
        return False
    return (meta.app_label, meta.object_name) in _TRACKED


def _snapshot_fields(instance) -> dict[str, Any]:
    """Simple field snapshot — skips reverse relations and unloaded FKs."""
    data = {}
    for field in instance._meta.fields:
        try:
            data[field.name] = getattr(instance, field.attname, None)
        except Exception:  # noqa: BLE001 — a bad field never blocks the audit
            data[field.name] = "<unavailable>"
    return data


def _diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list]:
    diff = {}
    for k, new_val in new.items():
        old_val = old.get(k)
        if old_val != new_val:
            diff[k] = [old_val, new_val]
    return diff


@receiver(pre_save)
def _capture_pre_save_snapshot(sender, instance, **kwargs):
    if not _is_tracked(sender):
        return
    if instance.pk is None:
        instance._audit_pre = None
        return
    try:
        current = sender.objects.get(pk=instance.pk)
        instance._audit_pre = _snapshot_fields(current)
    except sender.DoesNotExist:
        instance._audit_pre = None


@receiver(post_save)
def _record_save(sender, instance, created, **kwargs):
    if not _is_tracked(sender):
        return
    from core.audit_models import AuditLog

    actor = _current_actor.get()
    actor_label = _actor_label(actor)

    if created:
        AuditLog.record(
            action="create",
            instance=instance,
            changes={"created": True},
            actor=actor,
            actor_label=actor_label,
        )
        return

    pre = getattr(instance, "_audit_pre", None) or {}
    post = _snapshot_fields(instance)
    changes = _diff(pre, post)
    if changes:
        AuditLog.record(
            action="update",
            instance=instance,
            changes=changes,
            actor=actor,
            actor_label=actor_label,
        )


@receiver(post_delete)
def _record_delete(sender, instance, **kwargs):
    if not _is_tracked(sender):
        return
    from core.audit_models import AuditLog

    actor = _current_actor.get()
    AuditLog.record(
        action="delete",
        instance=instance,
        actor=actor,
        actor_label=_actor_label(actor),
    )


def _actor_label(actor) -> str:
    if actor is None:
        return ""
    return getattr(actor, "get_username", lambda: str(actor))() or str(actor)


class AuditActorMiddleware:
    """
    Stash the current request user into the contextvar so signal receivers
    can attribute the change without threading the user through every save.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        token = _current_actor.set(user if getattr(user, "is_authenticated", False) else None)
        try:
            return self.get_response(request)
        finally:
            _current_actor.reset(token)


__all__ = [
    "AuditActorMiddleware",
    "set_current_actor",
]
