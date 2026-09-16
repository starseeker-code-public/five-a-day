"""An explicit "delete this and everything protecting it" admin action.

Four models cannot be deleted from `/admin/` with the ordinary Delete button,
and both reasons for that are deliberate:

* `PaymentAdmin.has_delete_permission` refuses a collected, refunded or
  receipt-numbered row (`Payment.assert_deletable`) — a fiscal record must not
  be one click away from a permanent hole in the `YYYY-NNN` sequence;
* `Student`, `Parent` and `Enrollment` are PROTECTed by the rows that bill
  them, so Django refuses to cascade.

Django renders BOTH as «su cuenta no tiene permisos para borrar los siguientes
tipos de objetos», which reads as a broken account rather than as a rule, and
leaves an admin who genuinely needs the record gone — one created by mistake,
a test family predating go-live — with no route but a database shell. That is
how production's pre-launch test data came to be purged from a Cloud Run job.

This mixin adds a SECOND, explicitly named action beside the ordinary one: it
deletes the protecting rows in FK order and then the selection, behind a
confirmation page naming everything it is about to destroy. The default Delete
button keeps its guard — the protection was never meant to stop a deliberate,
named decision, only an accidental click.

Superuser-only, and the action is not even offered otherwise. `Teacher.admin`
mirrors onto `is_superuser`, so in practice that means any admin teacher; a
non-admin Teacher never reaches `/admin/` at all.

The module imports nothing but Django ON PURPOSE. `billing.admin` and
`students.admin` both use it, and naming `Payment` here would invert the
documented dependency flow (`students <- billing`) for one of them. Each
ModelAdmin declares instead the REVERSE ACCESSOR names of the rows that
protect it, which `_meta` resolves to a queryset with no import at all.
"""

from django.apps import apps
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.db import transaction
from django.template.response import TemplateResponse

PURGE_ACTION_NAME = "purge_with_history"


def _describe(deleted: dict[str, int]) -> str:
    """Django's `{"billing.Payment": 3}` as «3 Pagos», in Spanish, for the UI."""
    parts = [f"{count} {apps.get_model(label)._meta.verbose_name_plural}" for label, count in sorted(deleted.items())]
    return ", ".join(parts) if parts else "nada"


class PurgeWithHistoryMixin:
    """Adds the `purge_with_history` action. See the module docstring."""

    #: Reverse accessors of the rows that PROTECT this model, in the order they
    #: have to be deleted. Empty when nothing points here.
    purge_accessors: tuple[str, ...] = ()

    def has_purge_permission(self, request):
        """Django's `permissions=["purge"]` hook — it hides the action outright.

        Filtering here rather than overriding `get_actions` keeps the mixin off
        Django 6.1's two deprecated admin-action APIs (the `action_location`
        signature and unpacking an `Action` as a tuple), both of which go in 7.0.
        """
        return request.user.is_superuser

    def _purge_dependents(self, targets):
        """The protecting rows, as querysets, in delete order."""
        dependents = []
        for accessor in self.purge_accessors:
            rel = self.model._meta.get_field(accessor)
            dependents.append(rel.related_model._base_manager.filter(**{f"{rel.field.name}__in": targets}))
        return dependents

    @admin.action(description="Eliminar definitivamente (incluye pagos cobrados)", permissions=["purge"])
    def purge_with_history(self, request, queryset):
        if not request.user.is_superuser:
            self.message_user(
                request,
                "Solo un administrador puede eliminar registros junto con su historial.",
                level=messages.ERROR,
            )
            return None

        # Re-select by pk: a ModelAdmin queryset carries `select_related` and,
        # on EnrollmentAdmin, aggregate annotations — a GROUP BY that has no
        # business being in a DELETE.
        targets = self.model._base_manager.filter(pk__in=list(queryset.values_list("pk", flat=True)))
        dependents = self._purge_dependents(targets)

        if request.POST.get("purge_confirmed"):
            deleted: dict[str, int] = {}
            with transaction.atomic():
                for dependent in [*dependents, targets]:
                    for label, count in dependent.delete()[1].items():
                        deleted[label] = deleted.get(label, 0) + count
            self.message_user(request, f"Eliminado definitivamente: {_describe(deleted)}.", level=messages.WARNING)
            return None

        objects = list(targets)
        counts = [(qs.model._meta.verbose_name_plural, qs.count()) for qs in dependents]
        counts.append((self.model._meta.verbose_name_plural, len(objects)))
        return TemplateResponse(
            request,
            "admin/purge_confirmation.html",
            {
                **self.admin_site.each_context(request),
                "title": "Eliminar definitivamente",
                "objects": objects,
                "counts": counts,
                "opts": self.model._meta,
                "action_name": PURGE_ACTION_NAME,
                "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
                "selected": [obj.pk for obj in objects],
                "media": self.media,
            },
        )
