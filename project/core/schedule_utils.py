"""Shared schedule time/day mappings and helpers.

Single source of truth for how a `ScheduleSlot` (row, day, col) maps to a
human-readable day + time range, used by the weekly-schedule view and by the
welcome email (which shows the student's group schedule).

Monday–Thursday use three shared bands (`ROW_STARTS` / `ROW_ENDS`), the same
for both columns. Friday is different: the academy runs four sessions at
overlapping times, so its slots are keyed on (row, col) rather than sharing a
band — see `FRIDAY_TIMES`.
"""

from core.models import ScheduleSlot

# day: 0=Mon … 4=Fri
DAY_NAMES_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]

FRIDAY = 4

# row: 0, 1, 2 → time band (Mon–Thu).
ROW_STARTS = ["16:10", "17:40", "19:10"]
ROW_ENDS = ["17:30", "19:00", "20:30"]

NUM_ROWS = len(ROW_STARTS)
NUM_COLS = 2

# Friday runs four overlapping sessions, so each cell carries its own hours:
#   row 0 · col 0 → infantil      16:30–17:15
#   row 0 · col 1 → primaria      16:00–17:25
#   row 1 · col 0 → Fun Friday    17:30–18:30   (fixed, not group-assignable)
#   row 1 · col 1 → adultos       17:30–19:00
# Row 2 has no Friday session.
FRIDAY_TIMES = {
    (0, 0): ("16:30", "17:15"),
    (0, 1): ("16:00", "17:25"),
    (1, 0): ("17:30", "18:30"),
    (1, 1): ("17:30", "19:00"),
}

# The Friday cell reserved for Fun Friday. It is rendered as a fixed label and
# never offered as a group slot.
FUN_FRIDAY_CELL = (1, 0)

# Fallback used when a slot references a (row, day, col) outside the grid —
# only reachable from legacy rows written before the API validated its input.
_UNKNOWN_TIME = ("--:--", "--:--")


def is_valid_slot(row: int, day: int, col: int) -> bool:
    """True when (row, day, col) addresses a real cell in the weekly grid.

    Friday only has sessions in rows 0 and 1; every other day uses all three
    bands. Anything else is rejected by the save endpoint so a bad slot can't
    be persisted and blow up rendering for everyone.
    """
    if not (0 <= day < len(DAY_NAMES_ES)):
        return False
    if not (0 <= col < NUM_COLS):
        return False
    if day == FRIDAY:
        return (row, col) in FRIDAY_TIMES
    return 0 <= row < NUM_ROWS


def slot_time_range(row: int, day: int, col: int = 0) -> tuple[str, str]:
    """Return (start, end) time strings for a slot at (row, day, col).

    Never raises: an out-of-range slot yields a placeholder rather than an
    IndexError, because a single bad row used to 500 the whole schedule page
    (and the welcome email) for every user.
    """
    if day == FRIDAY:
        return FRIDAY_TIMES.get((row, col), _UNKNOWN_TIME)
    if 0 <= row < NUM_ROWS:
        return ROW_STARTS[row], ROW_ENDS[row]
    return _UNKNOWN_TIME


def get_group_schedule_lines(group) -> list[str]:
    """Human-readable schedule lines for a group's assigned slots.

    e.g. ``["Viernes de 16:30 a 17:15"]``. Returns an empty list when the
    group is None or has no slots. Duplicate day/time entries (e.g. the same
    band across both columns) are collapsed.
    """
    if group is None:
        return []

    lines: list[str] = []
    seen: set[str] = set()
    for slot in ScheduleSlot.objects.filter(group=group).order_by("day", "row", "col"):
        if not is_valid_slot(slot.row, slot.day, slot.col):
            continue
        start, end = slot_time_range(slot.row, slot.day, slot.col)
        line = f"{DAY_NAMES_ES[slot.day]} de {start} a {end}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines
