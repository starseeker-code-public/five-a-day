"""
Waiting list & group capacity — views.

Waiting-list students have `is_waiting=True` and no active enrollment. Their
`group` field represents their *preferred* group; when a spot opens the admin
promotes them via `assign_from_waiting_list`, which flips is_waiting off and
kicks the admin over to the standard update form to finalise the enrollment.
"""

import calendar
from datetime import date

from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from billing.models import Payment, SiteConfiguration
from billing.services.enrollment_service import EnrollmentService
from core.models import HistoryLog
from students.models import Group, Student


def _waiting_students_qs():
    """Active, waiting students sorted FIFO (oldest waiter first)."""
    return (
        Student.objects.filter(active=True, is_waiting=True)
        .select_related("group", "group__teacher")
        .prefetch_related("parents")
        .order_by("waiting_since", "created_at")
    )


def waiting_list_view(request):
    """List page for students on the waiting list, filterable by preferred group."""
    qs = _waiting_students_qs()

    group_filter = request.GET.get("group", "").strip()
    if group_filter:
        try:
            qs = qs.filter(group_id=int(group_filter))
        except ValueError:
            pass

    students = list(qs)
    groups = Group.objects.filter(active=True).select_related("teacher").order_by("group_name")

    groups_summary = []
    for group in groups:
        groups_summary.append(
            {
                "group": group,
                "enrolled": group.enrolled_count,
                "waiting": group.waiting_count,
                "max_students": group.max_students,
                "available_spots": group.available_spots,
                "is_full": group.is_full,
            }
        )

    return render(
        request,
        "waiting_list.html",
        {
            "waiting_students": students,
            "waiting_count": len(students),
            "groups": groups,
            "groups_summary": groups_summary,
            "selected_group_id": group_filter,
        },
    )


@require_http_methods(["POST"])
def assign_from_waiting_list(request, student_id):
    """
    Quick-assign a waiting-list student. Flips is_waiting=False and creates a
    default full-time monthly enrollment + pending enrollment-fee payment so the
    student ends up in a consistent state. Admin can later tweak the enrollment
    plan/discounts from the standard update flow.
    """
    student = get_object_or_404(
        Student.objects.select_related("group"),
        id=student_id,
        is_waiting=True,
    )

    # Defensive: Student.group is a required FK today, but if that ever
    # becomes nullable a missing group would produce an AttributeError on
    # `.is_full` below. Fail loudly with a friendly error instead.
    target_group = student.group
    if target_group is None:
        error_msg = "El estudiante no tiene grupo preferido asignado."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": error_msg}, status=400)
        messages.error(request, f"❌ {error_msg}")
        return redirect("waiting_list")

    # Enforce the group cap. `available_spots is None` means "no cap" — always allow.
    if target_group.is_full:
        error_msg = (
            f"El grupo {target_group.group_name} está completo "
            f"({target_group.enrolled_count}/{target_group.max_students})."
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": error_msg}, status=409)
        messages.error(request, f"❌ {error_msg}")
        return redirect("waiting_list")

    default_parent = student.parents.first()
    # Non-adult students must have at least one parent linked; otherwise the
    # generated Payment would be orphaned (Payment.parent is nullable but
    # tax certificates and reminders both key off parent).
    if default_parent is None and not student.is_adult:
        error_msg = (
            f"{student.full_name} no tiene padre/madre asociado — no se puede promover "
            "sin un titular para la matrícula."
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": error_msg}, status=400)
        messages.error(request, f"❌ {error_msg}")
        return redirect("waiting_list")

    try:
        with transaction.atomic():
            student.is_waiting = False
            student.save(update_fields=["is_waiting", "waiting_since", "updated_at"])

            enrollment = EnrollmentService.create_enrollment(
                student,
                {
                    "enrollment_plan": "monthly_full",
                    "has_language_cheque": False,
                    "is_sibling_discount": False,
                    "is_special": False,
                    "manual_amount": None,
                },
                is_adult=student.is_adult,
            )

            config = SiteConfiguration.get_config()
            today = date.today()
            last_day = calendar.monthrange(today.year, today.month)[1]
            due_date = date(today.year, today.month, last_day)
            # v1.13 — apply returning-student discount when applicable.
            enrollment_fee, returning_discount = EnrollmentService.compute_enrollment_fee(
                config, student, is_adult=student.is_adult
            )
            concept = f"Matrícula {enrollment.academic_year} — {student.full_name}"
            if returning_discount:
                concept += f" (dto. alumno recurrente −{returning_discount:.2f} €)"
            Payment.objects.create(
                student=student,
                parent=default_parent,
                enrollment=enrollment,
                payment_type="enrollment",
                payment_method="transfer",
                amount=enrollment_fee,
                currency="EUR",
                payment_status="pending",
                due_date=due_date,
                concept=concept,
            )

            HistoryLog.log(
                "waiting_list_assigned",
                f"Asignado desde lista de espera: {student.full_name} → {target_group.group_name}",
                icon="person_add",
            )
    except Exception as e:  # noqa: BLE001 — surface any failure to the user
        error_msg = f"Error al asignar al estudiante: {e}"
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": error_msg}, status=500)
        messages.error(request, f"❌ {error_msg}")
        return redirect("waiting_list")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse(
            {
                "success": True,
                "student_id": student.id,
                "redirect_url": reverse("student_detail", args=[student.id]),
            }
        )

    messages.success(
        request,
        (
            f"✅ {student.full_name} promovido/a al grupo {target_group.group_name}. "
            "Matrícula mensual creada — ajústala desde la ficha si es necesario."
        ),
    )
    return redirect("waiting_list")


@require_http_methods(["POST"])
def add_to_waiting_list(request, student_id):
    """Flip an existing student back onto the waiting list (rare — usually used on
    admin's request when a spot needs to be freed without deleting the student)."""
    student = get_object_or_404(Student, id=student_id, active=True)
    if student.is_waiting:
        messages.info(request, f"{student.full_name} ya está en la lista de espera.")
        return redirect("waiting_list")

    student.is_waiting = True
    # save() auto-sets waiting_since
    student.save(update_fields=["is_waiting", "waiting_since", "updated_at"])

    HistoryLog.log(
        "waiting_list_added",
        f"Movido a lista de espera: {student.full_name} (grupo preferido {student.group.group_name})",
        icon="hourglass_top",
    )

    messages.success(request, f"✅ {student.full_name} añadido/a a la lista de espera.")
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
                "name": group.group_name,
                "color": group.color,
                "enrolled": group.enrolled,
                "waiting": group.waiting,
                "max_students": group.max_students,
                "available": available,
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
    "assign_from_waiting_list",
    "add_to_waiting_list",
    "group_capacity_summary",
    "notify_capacity_freed",
]
