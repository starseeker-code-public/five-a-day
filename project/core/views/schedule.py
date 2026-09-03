import json
import logging

from django.db import models
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from core.models import FunFridayAttendance, HistoryLog, ScheduleSlot
from core.schedule_utils import is_valid_slot, slot_time_range
from core.views.students import get_ff_student_ids, get_last_friday, get_next_friday
from students.models import Group, Student

logger = logging.getLogger(__name__)


def schedule_view(request):
    """Vista del horario semanal estilo Google Calendar."""
    groups = (
        Group.objects.filter(active=True)
        .select_related("teacher")
        .prefetch_related(
            models.Prefetch("students", queryset=Student.objects.filter(active=True).order_by("first_name"))
        )
        .order_by("group_name")
    )

    groups_list = list(groups)
    groups_data = []
    for g in groups_list:
        groups_data.append(
            {
                "id": g.id,
                "name": g.group_name,
                "color": g.color,
                "teacher": g.teacher.first_name,
                "students": [s.first_name for s in g.students.all()],
            }
        )

    saved = ScheduleSlot.objects.select_related("group").all()
    slots_data = []
    for s in saved:
        if not is_valid_slot(s.row, s.day, s.col):
            continue  # legacy/out-of-grid row — skip rather than 500 the page
        start, end = slot_time_range(s.row, s.day, s.col)
        slots_data.append(
            {"row": s.row, "day": s.day, "col": s.col, "group_id": s.group_id, "start": start, "end": end}
        )

    # Only the students actually signed up for the coming Fun Friday. This used
    # to send every active student, so the dropdown listed the whole academy and
    # said nothing about who is attending.
    ff_student_ids = get_ff_student_ids(get_next_friday())
    ff_students_qs = Student.objects.filter(active=True, id__in=ff_student_ids).order_by("first_name", "last_name")
    students_data = [{"first_name": s.first_name, "last_name": s.last_name} for s in ff_students_qs]

    # Passed as plain objects (not json.dumps strings) so the template can use
    # `|json_script`, which escapes `<`, `>` and `&`. Inlining JSON with `|safe`
    # let a group or student name containing `</script>` break out of the block.
    return render(
        request,
        "schedule.html",
        {
            "groups_json": groups_data,
            "slots_json": slots_data,
            "students_json": students_data,
        },
    )


@require_http_methods(["POST"])
def save_schedule_slot(request):
    """Save a single schedule slot assignment to the database."""
    try:
        data = json.loads(request.body)
        row = int(data["row"])
        day = int(data["day"])
        col = int(data["col"])
        group_id = data.get("group_id")

        # Reject anything outside the grid. An unvalidated row used to be
        # accepted here and then raised IndexError on every render of
        # /schedule/ — a 500 for every user, with no UI to undo it.
        if not is_valid_slot(row, day, col):
            return JsonResponse(
                {"success": False, "error": "Franja horaria no válida."},
                status=400,
            )

        if group_id:
            group = get_object_or_404(Group, id=int(group_id))
            ScheduleSlot.objects.update_or_create(row=row, day=day, col=col, defaults={"group": group})
        else:
            ScheduleSlot.objects.filter(row=row, day=day, col=col).delete()

        HistoryLog.log_debounced(
            "schedule_updated",
            "Horario semanal actualizado",
            icon="calendar_month",
            minutes=5,
        )

        return JsonResponse({"success": True})
    except Http404:
        # The group lookup — a missing/deleted group is a 404, not a "no se pudo
        # guardar" masked by the catch-all (Http404 subclasses Exception).
        raise
    except Exception:
        logger.exception("Error saving schedule slot")
        return JsonResponse({"success": False, "error": "No se pudo guardar el horario."}, status=400)


def fun_friday_view(request):
    """Vista de Fun Friday con lista de estudiantes."""
    students = (
        Student.objects.filter(active=True, is_adult=False)
        .select_related("group")
        .order_by("group__group_name", "first_name")
    )
    this_friday = get_next_friday()
    last_friday = get_last_friday()

    # Single query for both weeks' attendance
    attendance = FunFridayAttendance.objects.filter(date__in=[this_friday, last_friday]).values_list(
        "student_id", "date"
    )
    this_week_ids = set()
    last_week_ids = set()
    for sid, att_date in attendance:
        if att_date == this_friday:
            this_week_ids.add(sid)
        else:
            last_week_ids.add(sid)

    # Filter from already-loaded students instead of re-querying
    students_list = list(students)
    this_week_students = [s for s in students_list if s.id in this_week_ids]
    last_week_students = [s for s in students_list if s.id in last_week_ids]

    return render(
        request,
        "fun_friday.html",
        {
            "students": students_list,
            "this_week_ids": this_week_ids,
            "last_week_ids": last_week_ids,
            "this_friday": this_friday,
            "last_friday": last_friday,
            "this_week_students": this_week_students,
            "last_week_students": last_week_students,
        },
    )
