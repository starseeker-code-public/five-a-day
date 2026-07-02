"""Tests for v1.1 — Waiting List & Group Capacity (models + helpers)."""

from datetime import date

import pytest

from core.models import HistoryLog
from core.views.waiting_list import group_capacity_summary, notify_capacity_freed
from students.models import Group, Student

pytestmark = pytest.mark.django_db


class TestGroupCapacityProperties:
    def test_no_cap_by_default(self, group):
        assert group.max_students == 0
        assert group.available_spots is None
        assert group.is_full is False

    def test_available_spots_with_cap(self, group, student):
        group.max_students = 3
        group.save()
        assert group.enrolled_count == 1
        assert group.available_spots == 2
        assert group.is_full is False

    def test_is_full_when_cap_reached(self, group, student):
        group.max_students = 1
        group.save()
        assert group.is_full is True
        assert group.available_spots == 0

    def test_waiting_students_excluded_from_enrolled_count(self, group, student):
        Student.objects.create(
            first_name="Espera",
            last_name="Test",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
            active=True,
            is_waiting=True,
        )
        assert group.enrolled_count == 1  # only `student`
        assert group.waiting_count == 1

    def test_inactive_students_excluded_from_enrolled_count(self, group, student, inactive_student):
        assert group.enrolled_count == 1  # inactive_student excluded


class TestStudentIsWaiting:
    def test_waiting_since_set_automatically(self, group):
        s = Student.objects.create(
            first_name="Ana",
            last_name="Test",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
            is_waiting=True,
        )
        assert s.waiting_since is not None

    def test_waiting_since_cleared_when_flipped_off(self, group):
        s = Student.objects.create(
            first_name="Ana",
            last_name="Test",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
            is_waiting=True,
        )
        original = s.waiting_since
        assert original is not None
        s.is_waiting = False
        s.save()
        s.refresh_from_db()
        assert s.waiting_since is None

    def test_waiting_since_not_reset_when_still_waiting(self, group):
        s = Student.objects.create(
            first_name="Ana",
            last_name="Test",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
            is_waiting=True,
        )
        original = s.waiting_since
        s.first_name = "Ana2"
        s.save()
        s.refresh_from_db()
        assert s.waiting_since == original


class TestGroupCapacitySummary:
    def test_returns_all_active_groups(self, group, teacher):
        Group.objects.create(group_name="Group B", color="#f00", teacher=teacher, active=True)
        Group.objects.create(group_name="Group C", color="#0f0", teacher=teacher, active=False)
        summary = group_capacity_summary()
        names = [row["name"] for row in summary]
        assert "Group A" in names
        assert "Group B" in names
        assert "Group C" not in names  # inactive excluded

    def test_has_room_for_waiters_true_when_waiter_and_no_cap(self, group, student):
        Student.objects.create(
            first_name="Waiter",
            last_name="One",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
            is_waiting=True,
        )
        row = next(r for r in group_capacity_summary() if r["id"] == group.id)
        assert row["waiting"] == 1
        assert row["has_room_for_waiters"] is True

    def test_has_room_for_waiters_false_when_full(self, group, student):
        group.max_students = 1
        group.save()
        Student.objects.create(
            first_name="Waiter",
            last_name="One",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
            is_waiting=True,
        )
        row = next(r for r in group_capacity_summary() if r["id"] == group.id)
        assert row["has_room_for_waiters"] is False


class TestNotifyCapacityFreed:
    def test_no_log_when_no_waiters(self, group, student):
        before = HistoryLog.objects.count()
        notify_capacity_freed(student)
        assert HistoryLog.objects.count() == before

    def test_logs_when_waiters_present(self, group, student):
        Student.objects.create(
            first_name="Waiter",
            last_name="One",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
            is_waiting=True,
        )
        before = HistoryLog.objects.count()
        notify_capacity_freed(student)
        assert HistoryLog.objects.count() == before + 1
        log = HistoryLog.objects.latest("id")
        assert log.action == "waiting_list_spot_open"
        assert group.group_name in log.message

    def test_signal_fires_on_deactivation_with_waiters(self, group, student):
        Student.objects.create(
            first_name="Waiter",
            last_name="One",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
            is_waiting=True,
        )
        before = HistoryLog.objects.filter(action="waiting_list_spot_open").count()
        student.active = False
        student.save()
        after = HistoryLog.objects.filter(action="waiting_list_spot_open").count()
        assert after == before + 1
