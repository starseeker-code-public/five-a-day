"""Shared schedule time/day mappings and helpers.

Single source of truth for how a `ScheduleSlot` (row, day, col) maps to a
human-readable day + time range, used by the weekly-schedule view and by the
welcome email (which shows the student's group schedule).
"""

from core.models import ScheduleSlot

# day: 0=Mon … 4=Fri
DAY_NAMES_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]

# row: 0, 1, 2 → time band (Mon–Thu). Fridays use a single earlier band.
ROW_STARTS = ["16:10", "17:40", "19:10"]
ROW_ENDS = ["17:30", "19:00", "20:30"]
FRI_START = "16:00"
FRI_END = "17:20"


def slot_time_range(row: int, day: int) -> tuple[str, str]:
    """Return (start, end) time strings for a slot at (row, day)."""
    if day == 4:
        return FRI_START, FRI_END
    return ROW_STARTS[row], ROW_ENDS[row]


def get_group_schedule_lines(group) -> list[str]:
    """Human-readable schedule lines for a group's assigned slots.

    e.g. ``["Viernes de 16:10 a 17:30"]``. Returns an empty list when the
    group is None or has no slots. Duplicate day/time entries (e.g. the same
    band across both columns) are collapsed.
    """
    if group is None:
        return []

    lines: list[str] = []
    seen: set[str] = set()
    for slot in ScheduleSlot.objects.filter(group=group).order_by("day", "row", "col"):
        if not (0 <= slot.day < len(DAY_NAMES_ES)):
            continue
        start, end = slot_time_range(slot.row, slot.day)
        line = f"{DAY_NAMES_ES[slot.day]} de {start} a {end}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines
