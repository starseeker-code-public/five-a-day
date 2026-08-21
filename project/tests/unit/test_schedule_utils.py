"""Tests for `core.schedule_utils` — the single source of truth for how a
`ScheduleSlot` (row, day, col) becomes a human-readable day + time range.

Used by both the weekly-schedule view and the welcome email, so a regression
here shows up in what parents are told their child's timetable is.
"""

import pytest

from core.models import ScheduleSlot
from core.schedule_utils import (
    DAY_NAMES_ES,
    FRI_END,
    FRI_START,
    ROW_ENDS,
    ROW_STARTS,
    get_group_schedule_lines,
    slot_time_range,
)

pytestmark = pytest.mark.django_db


class TestSlotTimeRange:
    @pytest.mark.parametrize("row", [0, 1, 2])
    @pytest.mark.parametrize("day", [0, 1, 2, 3])
    def test_mon_to_thu_uses_the_row_band(self, row, day):
        assert slot_time_range(row, day) == (ROW_STARTS[row], ROW_ENDS[row])

    @pytest.mark.parametrize("row", [0, 1, 2])
    def test_friday_ignores_the_row_and_uses_the_single_band(self, row):
        """Fridays run one earlier session regardless of row."""
        assert slot_time_range(row, 4) == (FRI_START, FRI_END)

    def test_bands_are_ordered_and_non_overlapping(self):
        assert ROW_STARTS == sorted(ROW_STARTS)
        for start, end in zip(ROW_STARTS, ROW_ENDS, strict=True):
            assert start < end
        for earlier_end, later_start in zip(ROW_ENDS, ROW_STARTS[1:], strict=False):
            assert earlier_end <= later_start


class TestGetGroupScheduleLines:
    def test_none_group_returns_empty(self):
        assert get_group_schedule_lines(None) == []

    def test_group_without_slots_returns_empty(self, group):
        assert get_group_schedule_lines(group) == []

    def test_single_slot_is_rendered_in_spanish(self, group):
        ScheduleSlot.objects.create(row=0, day=0, col=0, group=group)
        assert get_group_schedule_lines(group) == ["Lunes de 16:10 a 17:30"]

    def test_friday_slot_uses_the_friday_band(self, group):
        ScheduleSlot.objects.create(row=2, day=4, col=0, group=group)
        assert get_group_schedule_lines(group) == [f"Viernes de {FRI_START} a {FRI_END}"]

    def test_duplicate_day_and_band_across_columns_is_collapsed(self, group):
        """The same band in both columns is one timetable entry, not two."""
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

    def test_other_groups_slots_are_excluded(self, group, teacher):
        from students.models import Group

        other = Group.objects.create(group_name="Group B", color="#000000", teacher=teacher, active=True)
        ScheduleSlot.objects.create(row=0, day=0, col=0, group=group)
        ScheduleSlot.objects.create(row=1, day=3, col=0, group=other)
        assert get_group_schedule_lines(group) == ["Lunes de 16:10 a 17:30"]

    def test_day_names_cover_the_working_week(self):
        assert DAY_NAMES_ES == ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
