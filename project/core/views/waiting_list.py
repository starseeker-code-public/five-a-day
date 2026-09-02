"""
Waiting list & group capacity — views.

Waiting-list students have `is_waiting=True` and no active enrollment. Their
`group` field represents their *preferred* group; when a spot opens the admin
hits `assign_from_waiting_list`, which does NOT promote the entry in place —
it redirects into the normal "Matricular" flow (parent → student) so the new
student is created properly, with a parent/tutor. The waiting entry is deleted
once that student is saved.
"""

import logging

from django.contrib import messages
from django.db import transaction
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from core.models import HistoryLog
from students.forms import WaitingListForm
from students.models import Group, Student

logger = logging.getLogger(__name__)


def _waiting_students_qs():
    """Active, waiting students: priority entries first, then FIFO within each band.

    `waiting_priority` is what makes the checkbox mean something — without it in
    the ordering the flag would be decorative, and the admin offering the next
    free spot works straight down this list.
    """
    return (
        Student.objects.filter(active=True, is_waiting=True)
        .select_related("group", "group__teacher")
        .prefetch_related("parents")
        .order_by("-waiting_priority", "waiting_since", "created_at")
    )


def waiting_list_view(request):
    """List page for students on the waiting list, filterable by preferred group."""
    qs = _waiting_students_qs()

    group_filter = request.GET.get("group", "").strip()
    if group_filter:
        try:
            qs = qs.filter(group_id=int(group_filter))
        except ValueError:
            # Non-numeric ?group= in a hand-edited URL: ignore the filter and
            # show the unfiltered list rather than 500-ing on a bad query param.
            pass

    students = list(qs)

    # `group_capacity_summary()` resolves every group's enrolled/waiting counts in
    # ONE annotated query. This loop used to read `group.enrolled_count`,
    # `waiting_count`, `available_spots` and `is_full` off each Group — four
    # uncached `.count()` properties, and `available_spots`/`is_full` each
    # recompute `enrolled_count`, so it cost FOUR queries per group (53 for 13
    # groups) on the one page whose entire purpose is showing capacity. The
    # helper existed precisely to avoid this and simply was not called here.
    groups_summary = group_capacity_summary()

    # Per-student capacity comes out of the same rows. `student.group.is_full` in
    # the template was another four queries per student row.
    #
    # A student whose preferred group is INACTIVE gets `None` here, so their row
    # shows no capacity hint and the Matricular button is not greyed out. That is
    # cosmetic only: `assign_from_waiting_list` re-checks `target_group.is_full`
    # server side, and that check is the authoritative one.
    capacity_by_group = {row["id"]: row for row in groups_summary}
    for student in students:
        student.group_capacity = capacity_by_group.get(student.group_id)

    return render(
        request,
        "waiting_list.html",
        {
            "waiting_students": students,
            "waiting_count": len(students),
            # The filter dropdown reuses the groups already fetched above.
            "groups": [row["group"] for row in groups_summary],
            "groups_summary": groups_summary,
            "selected_group_id": group_filter,
        },
    )


@require_http_methods(["GET", "POST"])
def waiting_list_create(request):
    """Short form for adding someone to the waiting list.

    Only a name and a phone number are required — these are taken during a
    phone call. The previous route through `StudentCreateView?mode=waiting`
    reused the full enrollment form and demanded a group, a school, a birth
    date and GDPR consent before it would save anything.
    """
    if request.method == "POST":
        form = WaitingListForm(request.POST)
        if form.is_valid():
            student = form.save()
            group_label = student.group.group_name if student.group_id else "sin grupo preferido"
            priority_label = " (prioritario)" if student.waiting_priority else ""
            HistoryLog.log(
                "waiting_list_added",
                f"Nuevo en lista de espera: {student.full_name} — {group_label}{priority_label}",
                icon="hourglass_top",
            )
            messages.success(request, f"✅ {student.full_name} añadido/a a la lista de espera.")
            return redirect("waiting_list")
        messages.error(request, "Revisa los campos obligatorios.")
    else:
        form = WaitingListForm()

    return render(request, "waiting_list_create.html", {"form": form})


@require_http_methods(["GET", "POST"])
def assign_from_waiting_list(request, student_id):
    """
    Start the real enrollment for a waiting-list entry.

    This used to promote the waiting entry in place: it flipped `is_waiting`
    off and created an enrollment + payments straight away. A waiting entry is
    taken over the phone with only a name and a contact number, so the result
    was a student with **no parent/guardian** and a payment with no titular.
    Now the button only redirects into the normal "Matricular" flow
    (parent → student), carrying `?from_waiting=<id>` so both forms can
    prefill from the waiting entry and the old entry is removed once the new
    student is saved.
    """
    student = get_object_or_404(
        Student.objects.select_related("group"),
        id=student_id,
        is_waiting=True,
    )

    target_group = student.group
    if target_group is not None and target_group.is_full:
        messages.error(
            request,
            (
                f"❌ El grupo {target_group.group_name} está completo "
                f"({target_group.enrolled_count}/{target_group.max_students})."
            ),
        )
        return redirect("waiting_list")

    return redirect(f"{reverse('parent_create')}?from_waiting={student.id}")


def waiting_entry_from_request(request):
    """Return the waiting-list Student referenced by `?from_waiting=<id>`, or None.

    Shared by `ParentCreateView` and `StudentCreateView` to prefill the
    enrollment forms from a waiting-list entry. Silently returns None for a
    missing/!numeric/already-promoted id — the forms then behave as a normal
    creation instead of 500-ing on a hand-edited URL.
    """
    raw_id = request.GET.get("from_waiting") or request.POST.get("from_waiting")
    if not raw_id:
        return None
    try:
        return Student.objects.select_related("group").get(id=int(raw_id), is_waiting=True, active=True)
    except (Student.DoesNotExist, TypeError, ValueError):
        return None


def discard_waiting_entry(waiting, new_student=None):
    """Remove a waiting-list entry once its real student record has been created.

    The entry is deleted outright when nothing references it (the normal case:
    a phone entry with no enrollment and no payments). A student who was moved
    *back* onto the waiting list keeps their payment history, and `Payment` /
    `Enrollment` protect their FK to Student, so those are archived instead
    (`active=False`) rather than blowing up the enrollment that just succeeded.
    """
    label = waiting.full_name
    try:
        with transaction.atomic():
            waiting.parents.clear()
            waiting.delete()
        outcome = "eliminada"
    except ProtectedError:
        waiting.active = False
        waiting.is_waiting = False
        waiting.save(update_fields=["active", "is_waiting", "updated_at"])
        outcome = "archivada (tenía pagos asociados)"

    detail = f" → nueva ficha: {new_student.full_name}" if new_student else ""
    HistoryLog.log(
        "waiting_list_assigned",
        f"Lista de espera {outcome}: {label}{detail}",
        icon="person_add",
    )


@require_http_methods(["POST"])
def add_to_waiting_list(request, student_id):
    """Flip an existing student back onto the waiting list (rare — usually used on
    admin's request when a spot needs to be freed without deleting the student)."""
    student = get_object_or_404(Student, id=student_id, active=True)
    if student.is_waiting:
        messages.info(request, f"{student.full_name} ya está en la lista de espera.")
        return redirect("waiting_list")

    with transaction.atomic():
        student.is_waiting = True
        # save() auto-sets waiting_since
        student.save(update_fields=["is_waiting", "waiting_since", "updated_at"])

        # Cancel the active enrollment. Leaving it active was a one-way door:
        # the student kept generating pending payments while off the roster,
        # and `assign_from_waiting_list` later hit the
        # unique_active_enrollment_per_student constraint and 500'd, so they
        # could never be promoted back.
        cancelled = student.enrollments.filter(status="active").update(status="cancelled")

    group_name = student.group.group_name if student.group else "sin grupo"
    HistoryLog.log(
        "waiting_list_added",
        f"Movido a lista de espera: {student.full_name} (grupo preferido {group_name})",
        icon="hourglass_top",
    )

    note = " Su matrícula activa se ha cancelado." if cancelled else ""
    messages.success(request, f"✅ {student.full_name} añadido/a a la lista de espera.{note}")
    return redirect("waiting_list")


def group_capacity_summary():
    """
    Helper used by the dashboard widget — returns groups with capacity info and a
    flag for those that have room while also having waiters.

    Uses annotations to avoid N+1 counts over `group.enrolled_count` /
    `group.waiting_count` when many groups are shown.
    """
    from django.db.models import Count, Q

    qs = (
        Group.objects.filter(active=True)
        .annotate(
            enrolled=Count(
                "students",
                filter=Q(students__active=True, students__is_waiting=False),
                distinct=True,
            ),
            waiting=Count(
                "students",
                filter=Q(students__active=True, students__is_waiting=True),
                distinct=True,
            ),
        )
        .select_related("teacher")
        .order_by("group_name")
    )

    summary = []
    for group in qs:
        available = None
        if group.max_students:
            available = max(group.max_students - group.enrolled, 0)
        summary.append(
            {
                "id": group.id,
                # The Group itself, so a caller needing colour/teacher/name does not
                # re-query. `waiting_list_view` renders all three.
                "group": group,
                "name": group.group_name,
                "color": group.color,
                "enrolled": group.enrolled,
                "waiting": group.waiting,
                "max_students": group.max_students,
                "available": available,
                "is_full": bool(group.max_students) and group.enrolled >= group.max_students,
                "has_room_for_waiters": bool(group.waiting) and (available is None or available > 0),
            }
        )
    return summary


def notify_capacity_freed(student):
    """
    Called after a student is deactivated. Logs a HistoryLog notification when
    the student's group now has waiting-list candidates that could take the spot.

    Idempotent — the caller is responsible for only invoking this when the
    student transitions active True → False.
    """
    if not student.group_id:
        return
    group = student.group
    # Refresh counts — the student we just deactivated should already be excluded
    # by `active=True` in `enrolled_count`.
    waiting_candidates = group.students.filter(active=True, is_waiting=True).count()
    if not waiting_candidates:
        return

    HistoryLog.log(
        "waiting_list_spot_open",
        (
            f"Hueco disponible en {group.group_name} — "
            f"{waiting_candidates} estudiante{'s' if waiting_candidates != 1 else ''} en lista de espera."
        ),
        icon="notifications_active",
    )


__all__ = [
    "waiting_list_view",
    "waiting_list_create",
    "assign_from_waiting_list",
    "add_to_waiting_list",
    "waiting_entry_from_request",
    "discard_waiting_entry",
    "group_capacity_summary",
    "notify_capacity_freed",
]
