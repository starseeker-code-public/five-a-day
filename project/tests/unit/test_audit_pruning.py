"""
Tests for `core.tasks.prune_audit_log` and its management-command wrapper.

This is the only code path in the project that deletes from `audit_logs`, a
table the admin deliberately makes immutable (no add, no change, no delete —
`core.admin.AuditLogAdmin`). It had zero test references for eight versions
while permanently destroying rows, and `*/admin.py` being coverage-omitted
meant nothing else exercised the surrounding rules either.

`AuditLog.created_at` is `auto_now_add`, so rows cannot be created with a past
timestamp — every test here backdates with `queryset.update()` after creating.
"""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.models import AuditLog
from core.tasks import (
    AUDIT_LOG_RETENTION_DAYS,
    MIN_AUDIT_RETENTION_DAYS,
    prune_audit_log,
)

pytestmark = pytest.mark.django_db


def _entry(days_old: int, action: str = "update") -> AuditLog:
    """Create an AuditLog row aged `days_old` days."""
    row = AuditLog.objects.create(
        action=action,
        model="students.Student",
        object_id="1",
        object_label="Alumno de prueba",
        changes={"first_name": ["Ana", "Ana María"]},
    )
    AuditLog.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=days_old))
    row.refresh_from_db()
    return row


class TestPruneAuditLog:
    def test_deletes_rows_older_than_the_window_and_keeps_the_rest(self):
        old = _entry(AUDIT_LOG_RETENTION_DAYS + 10)
        boundary = _entry(AUDIT_LOG_RETENTION_DAYS - 10)
        fresh = _entry(1)

        result = prune_audit_log.apply().get()

        assert result["status"] == "success"
        assert result["deleted"] == 1
        assert not AuditLog.objects.filter(pk=old.pk).exists()
        assert AuditLog.objects.filter(pk=boundary.pk).exists()
        assert AuditLog.objects.filter(pk=fresh.pk).exists()

    def test_default_window_is_two_years(self):
        assert AUDIT_LOG_RETENTION_DAYS == 730

    def test_empty_table_is_a_no_op(self):
        result = prune_audit_log.apply().get()
        assert result["deleted"] == 0

    def test_honours_an_explicit_longer_window(self):
        row = _entry(500)

        # 500 days old, so a 450-day window prunes it and the default does not.
        assert prune_audit_log.apply(kwargs={"days": AUDIT_LOG_RETENTION_DAYS}).get()["deleted"] == 0
        assert AuditLog.objects.filter(pk=row.pk).exists()

        assert prune_audit_log.apply(kwargs={"days": 450}).get()["deleted"] == 1
        assert not AuditLog.objects.filter(pk=row.pk).exists()


class TestRetentionFloor:
    """A window short enough to erase the current course is refused.

    `days=0` would delete every row including today's, and a negative value
    reaches into the future. Neither is a retention policy; both are a typo,
    and either one erases the entries incriminating whoever ran it.
    """

    @pytest.mark.parametrize("days", [0, -1, 1, 30, MIN_AUDIT_RETENTION_DAYS - 1])
    def test_refuses_a_window_below_the_floor(self, days):
        fresh = _entry(1)

        with pytest.raises(ValueError, match="days must be >="):
            prune_audit_log.apply(kwargs={"days": days}, throw=True).get()

        assert AuditLog.objects.filter(pk=fresh.pk).exists()

    def test_accepts_the_floor_itself(self):
        result = prune_audit_log.apply(kwargs={"days": MIN_AUDIT_RETENTION_DAYS}).get()
        assert result["status"] == "success"

    def test_floor_still_protects_a_full_academic_year(self):
        # A course runs September–June; the floor must exceed it with a margin
        # so a prune can never destroy the trail for the course being taught.
        assert MIN_AUDIT_RETENTION_DAYS > 365


class TestDryRun:
    def test_dry_run_reports_without_deleting(self):
        old = _entry(AUDIT_LOG_RETENTION_DAYS + 5)
        _entry(AUDIT_LOG_RETENTION_DAYS + 5)
        fresh = _entry(1)

        result = prune_audit_log.apply(kwargs={"dry_run": True}).get()

        assert result["dry_run"] is True
        assert result["deleted"] == 0
        assert result["would_delete"] == 2
        assert AuditLog.objects.filter(pk=old.pk).exists()
        assert AuditLog.objects.filter(pk=fresh.pk).exists()
        assert AuditLog.objects.count() == 3

    def test_dry_run_count_matches_what_a_real_run_deletes(self):
        for _ in range(3):
            _entry(AUDIT_LOG_RETENTION_DAYS + 5)
        _entry(1)

        would = prune_audit_log.apply(kwargs={"dry_run": True}).get()["would_delete"]
        did = prune_audit_log.apply().get()["deleted"]

        assert would == did == 3


class TestManagementCommand:
    """Production has no Beat process: Cloud Scheduler runs this wrapper."""

    def test_command_prunes(self):
        old = _entry(AUDIT_LOG_RETENTION_DAYS + 5)
        fresh = _entry(1)

        call_command("prune_audit_log")

        assert not AuditLog.objects.filter(pk=old.pk).exists()
        assert AuditLog.objects.filter(pk=fresh.pk).exists()

    def test_command_dry_run_deletes_nothing(self):
        old = _entry(AUDIT_LOG_RETENTION_DAYS + 5)

        call_command("prune_audit_log", "--dry-run")

        assert AuditLog.objects.filter(pk=old.pk).exists()

    def test_command_surfaces_the_floor_as_a_command_error(self):
        """A Cloud Run Job should report a clean failure, not a Celery traceback."""
        fresh = _entry(1)

        with pytest.raises(CommandError, match="days must be >="):
            call_command("prune_audit_log", "--days", "0")

        assert AuditLog.objects.filter(pk=fresh.pk).exists()
