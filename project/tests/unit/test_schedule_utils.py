"""Tests for `core.schedule_utils` — the single source of truth for how a
`ScheduleSlot` (row, day, col) becomes a human-readable day + time range.

Used by both the weekly-schedule view and the welcome email, so a regression
here shows up in what parents are told their child's timetable is.

Monday-Thursday share three row bands across both columns. Friday is keyed on
(row, col) because the academy runs four overlapping sessions that day.
"""

import pytest

from core.models import ScheduleSlot
from core.schedule_utils import (
    DAY_NAMES_ES,
    FRIDAY,
    FRIDAY_TIMES,
    FUN_FRIDAY_CELL,
    NUM_COLS,
    NUM_ROWS,
    ROW_ENDS,
    ROW_STARTS,
    get_group_schedule_lines,
    is_valid_slot,
    slot_time_range,
)

pytestmark = pytest.mark.django_db


class TestSlotTimeRange:
    @pytest.mark.parametrize("row", [0, 1, 2])
    @pytest.mark.parametrize("day", [0, 1, 2, 3])
    @pytest.mark.parametrize("col", [0, 1])
    def test_mon_to_thu_uses_the_row_band_for_both_columns(self, row, day, col):
        assert slot_time_range(row, day, col) == (ROW_STARTS[row], ROW_ENDS[row])

    @pytest.mark.parametrize(
        ("row", "col", "expected"),
        [(row, col, times) for (row, col), times in sorted(FRIDAY_TIMES.items())],
    )
    def test_friday_cells_have_their_own_hours(self, row, col, expected):
        """Friday's four sessions overlap, so each cell carries its own times."""
        assert slot_time_range(row, FRIDAY, col) == expected

    def test_friday_columns_differ_in_the_same_row(self):
        """The whole point of the per-cell map: infantil and primaria run at
        different times in the same Friday row."""
        assert slot_time_range(0, FRIDAY, 0) != slot_time_range(0, FRIDAY, 1)

    def test_friday_row_two_has_no_session(self):
        assert not is_valid_slot(2, FRIDAY, 0)

    @pytest.mark.parametrize(("row", "day", "col"), [(99, 0, 0), (-1, 0, 0), (5, FRIDAY, 0)])
    def test_out_of_range_never_raises(self, row, day, col):
        """A stale/poisoned row must not IndexError: doing so 500'd the whole
        schedule page and the welcome email for every user."""
        start, end = slot_time_range(row, day, col)
        assert (start, end) == ("--:--", "--:--")

    def test_mon_to_thu_bands_are_ordered_and_non_overlapping(self):
        assert ROW_STARTS == sorted(ROW_STARTS)
        for start, end in zip(ROW_STARTS, ROW_ENDS, strict=True):
            assert start < end
        for earlier_end, later_start in zip(ROW_ENDS, ROW_STARTS[1:], strict=False):
            assert earlier_end <= later_start

    def test_every_friday_cell_starts_before_it_ends(self):
        for start, end in FRIDAY_TIMES.values():
            assert start < end


class TestIsValidSlot:
    @pytest.mark.parametrize("day", [0, 1, 2, 3])
    def test_all_rows_and_cols_valid_mon_to_thu(self, day):
        for row in range(NUM_ROWS):
            for col in range(NUM_COLS):
                assert is_valid_slot(row, day, col)

    def test_only_mapped_friday_cells_are_valid(self):
        for row in range(NUM_ROWS):
            for col in range(NUM_COLS):
                assert is_valid_slot(row, FRIDAY, col) is ((row, col) in FRIDAY_TIMES)

    @pytest.mark.parametrize(
        ("row", "day", "col"),
        [(0, -1, 0), (0, 5, 0), (0, 99, 0), (-1, 0, 0), (3, 0, 0), (99, 0, 0), (0, 0, -1), (0, 0, 2)],
    )
    def test_rejects_out_of_grid(self, row, day, col):
        assert not is_valid_slot(row, day, col)

    def test_fun_friday_cell_is_a_real_cell(self):
        """It is reserved for the Fun Friday label, but it is still a grid
        position — the renderer needs its hours."""
        assert FUN_FRIDAY_CELL in FRIDAY_TIMES


class TestGetGroupScheduleLines:
    def test_none_group_returns_empty(self):
        assert get_group_schedule_lines(None) == []

    def test_group_without_slots_returns_empty(self, group):
        assert get_group_schedule_lines(group) == []

    def test_single_slot_is_rendered_in_spanish(self, group):
        ScheduleSlot.objects.create(row=0, day=0, col=0, group=group)
        assert get_group_schedule_lines(group) == ["Lunes de 16:10 a 17:30"]

    def test_friday_slot_uses_its_own_cell_times(self, group):
        ScheduleSlot.objects.create(row=0, day=FRIDAY, col=1, group=group)
        assert get_group_schedule_lines(group) == ["Viernes de 16:00 a 17:25"]

    def test_two_friday_columns_are_two_distinct_lines(self, group):
        """Unlike Mon-Thu, Friday columns are different sessions and must not
        collapse into one timetable entry."""
        ScheduleSlot.objects.create(row=0, day=FRIDAY, col=0, group=group)
        ScheduleSlot.objects.create(row=0, day=FRIDAY, col=1, group=group)
        assert get_group_schedule_lines(group) == [
            "Viernes de 16:30 a 17:15",
            "Viernes de 16:00 a 17:25",
        ]

    def test_duplicate_day_and_band_across_columns_is_collapsed(self, group):
        """The same Mon-Thu band in both columns is one entry, not two."""
        ScheduleSlot.objects.create(row=0, day=1, col=0, group=group)
        ScheduleSlot.objects.create(row=0, day=1, col=1, group=group)
        assert get_group_schedule_lines(group) == ["Martes de 16:10 a 17:30"]

    def test_multiple_days_are_ordered_by_day_then_row(self, group):
        ScheduleSlot.objects.create(row=1, day=2, col=0, group=group)
        ScheduleSlot.objects.create(row=0, day=0, col=0, group=group)
        assert get_group_schedule_lines(group) == [
            "Lunes de 16:10 a 17:30",
            "Miércoles de 17:40 a 19:00",
        ]

    @pytest.mark.parametrize("bad_day", [-1, 5, 99])
    def test_out_of_range_day_is_skipped_not_crashed(self, group, bad_day):
        """A stale row with a day outside Mon-Fri must not raise IndexError."""
        ScheduleSlot.objects.create(row=0, day=bad_day, col=0, group=group)
        assert get_group_schedule_lines(group) == []

    @pytest.mark.parametrize(("bad_row", "day"), [(99, 0), (2, FRIDAY)])
    def test_out_of_range_row_is_skipped_not_crashed(self, group, bad_row, day):
        """Row 99 (any day) and row 2 on Friday address no real session."""
        ScheduleSlot.objects.create(row=bad_row, day=day, col=0, group=group)
        assert get_group_schedule_lines(group) == []

    def test_other_groups_slots_are_excluded(self, group, teacher):
        from students.models import Group

        other = Group.objects.create(group_name="Group B", color="#000000", teacher=teacher, active=True)
        ScheduleSlot.objects.create(row=0, day=0, col=0, group=group)
        ScheduleSlot.objects.create(row=1, day=3, col=0, group=other)
        assert get_group_schedule_lines(group) == ["Lunes de 16:10 a 17:30"]

    def test_day_names_cover_the_working_week(self):
        assert DAY_NAMES_ES == ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
