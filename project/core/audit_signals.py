"""
Signal receivers that populate the AuditLog (v1.10).

Registered in `core.apps.CoreConfig.ready()` so the receivers are loaded
exactly once, without polluting model import time.
"""

from __future__ import annotations

import contextvars
from typing import Any

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

# Actor propagated from the middleware. `contextvars.ContextVar` is
# request-local under WSGI + async-safe under ASGI — safer than threadlocals.
_current_actor: contextvars.ContextVar = contextvars.ContextVar("audit_actor", default=None)

# Models we track + the fields worth capturing per model.
#
# Two goals for the allow-list:
#   1. GDPR / PII minimisation — never persist password hashes, IBANs, DNIs,
#      or auth tokens into the audit blob. Structural fields (name, status,
#      flags) are what admins actually need to trace "who changed what".
#   2. Performance / storage — keep the JSON payload small so `AuditLog`
#      remains searchable and doesn't balloon the DB.
# Value is either a tuple of field names or the sentinel "__all__" meaning
# "capture every concrete field on the model" (only used for SiteConfiguration
# whose columns are all config knobs, no PII).
_TrackedFields = tuple[str, ...] | str
_TRACKED: dict[tuple[str, str], _TrackedFields] = {
    ("students", "Student"): (
        "first_name",
        "last_name",
        "birth_date",
        "gender",
        "is_adult",
        "school",
        "gdpr_signed",
        "group_id",
        "active",
        "is_waiting",
        "waiting_since",
        "withdrawal_date",
        "withdrawal_reason",
    ),
    ("students", "Parent"): (
        # Deliberately excludes `dni`, `iban`, `phone`, `email`, `sms_opt_in`
        # to avoid GDPR-sensitive PII in the audit trail.
        "first_name",
        "last_name",
    ),
    ("students", "Teacher"): (
        # Excludes `user_id` (link to auth.User whose password lives there),
        # `email`, `phone`.
        "first_name",
        "last_name",
        "active",
        "admin",
    ),
    ("students", "Group"): (
        "group_name",
        "color",
        "teacher_id",
        "max_students",
        "active",
    ),
    ("billing", "Enrollment"): (
        "student_id",
        "enrollment_type_id",
        "schedule_type",
        "payment_modality",
        "has_language_cheque",
        "is_sibling_discount",
        "enrollment_amount",
        "discount_percentage",
        "final_amount",
        "status",
        "enrollment_date",
        "academic_year",
    ),
    ("billing", "Payment"): (
        "student_id",
        "enrollment_id",
        "parent_id",
        "payment_type",
        "payment_method",
        "amount",
        "currency",
        "payment_status",
        "due_date",
        "payment_date",
        "concept",
    ),
    ("billing", "SiteConfiguration"): "__all__",
    ("billing", "Expense"): (
        "description",
        "category",
        "amount",
        "expense_date",
        "is_recurring",
        "recurring_day",
    ),
}


def set_current_actor(user):
    _current_actor.set(user)


def _is_tracked(sender) -> bool:
    meta = getattr(sender, "_meta", None)
    if meta is None:
        return False
    return (meta.app_label, meta.object_name) in _TRACKED


def _tracked_fields(instance) -> _TrackedFields | None:
    """Return the field allow-list to audit for `instance` (a tuple of field
    names or the "__all__" sentinel), or None if the model isn't tracked."""
    key = (instance._meta.app_label, instance._meta.object_name)
    return _TRACKED.get(key)


def _snapshot_fields(instance) -> dict[str, Any]:
    """Field snapshot honouring the per-model allow-list. `"__all__"` means
    every concrete field; otherwise only the named fields are captured."""
    tracked = _tracked_fields(instance)
    if tracked is None:
        return {}

    if tracked == "__all__":
        field_names = [f.name for f in instance._meta.fields]
    else:
        field_names = list(tracked)

    data = {}
    for name in field_names:
        try:
            value = getattr(instance, name, None)
        except Exception:  # noqa: BLE001 — a bad field never blocks the audit
            value = "<unavailable>"
        # JSON-safe representation of Decimals / dates / model instances
        if hasattr(value, "isoformat"):
            data[name] = value.isoformat()
        elif hasattr(value, "pk"):
            data[name] = value.pk
        else:
            data[name] = value if value is None or isinstance(value, str | int | float | bool) else str(value)
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
        # Reuse the row an earlier pre_save receiver already fetched, when there is
        # one — `students.models._capture_active_transition` publishes it. Falls
        # back to its own read, so nothing here depends on receiver ordering.
        current = getattr(instance, "_presave_db_obj", None)
        if not isinstance(current, sender):
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
