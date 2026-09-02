"""`backup_retention` — the scheduled port of scripts/backup_retention.sh.

The docs said "schedule daily" for months while nothing did, so the biweekly
and monthly recovery points did not exist. These tests drive the policy through
a fake SQL Admin client: create tier points on the right calendar days, never
double-create on a retried run, prune each class to its keep-count newest-first,
and touch nothing without --apply.
"""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

from core.management.commands.backup_retention import (
    KEEP_DAILY_AUTOMATED,
    Command,
    classify,
)


class FakeClient:
    def __init__(self, backups=None, retained=KEEP_DAILY_AUTOMATED):
        self.backups = backups if backups is not None else []
        self.retained = retained
        self.created: list[str] = []
        self.deleted: list[str] = []

    def instance(self):
        return {"settings": {"backupConfiguration": {"backupRetentionSettings": {"retainedBackups": self.retained}}}}

    def list_backups(self):
        return list(self.backups)

    def create_backup(self, description):
        self.created.append(description)
        self.backups.insert(
            0,
            {
                "id": f"new-{len(self.created)}",
                "description": description,
                "status": "SUCCESSFUL",
                "type": "ON_DEMAND",
                "windowStartTime": "2099-01-01T00:00:00Z",
            },
        )
        return {"name": f"op-{len(self.created)}"}

    def delete_backup(self, backup_id):
        self.deleted.append(backup_id)

    def operation(self, name):
        return {"status": "DONE"}


def _backup(bid, desc, when, status="SUCCESSFUL", btype="ON_DEMAND"):
    return {"id": bid, "description": desc, "windowStartTime": when, "status": status, "type": btype}


def _run(fake, day, *args):
    """Run the command on a pinned calendar day with the fake client."""
    import datetime

    out = StringIO()
    with (
        patch.object(Command, "client_class", lambda *a: fake),
        patch("core.management.commands.backup_retention.timezone.localdate", return_value=datetime.date(2026, *day)),
    ):
        call_command("backup_retention", *args, stdout=out, stderr=out)
    return out.getvalue()


class TestClassify:
    @pytest.mark.parametrize(
        ("desc", "want"),
        [
            ("tier:biweekly 2026-09-16", "biweekly"),
            ("tier:monthly 2026-08-31", "monthly"),
            ("pre-deploy v1.23.0 (67e7cb2)", "manual"),
            ("", "manual"),
        ],
    )
    def test_description_prefixes(self, desc, want):
        assert classify(desc) == want


class TestTierCreation:
    def test_biweekly_created_on_the_first_and_sixteenth(self):
        for day in [(9, 1), (9, 16)]:
            fake = FakeClient()
            _run(fake, day, "--apply")
            assert any(d.startswith("tier:biweekly") for d in fake.created), day

    def test_monthly_created_on_the_last_day(self):
        fake = FakeClient()
        _run(fake, (9, 30), "--apply")
        assert fake.created == ["tier:monthly 2026-09-30"]

    def test_ordinary_days_only_prune(self):
        fake = FakeClient()
        out = _run(fake, (9, 10), "--apply")
        assert fake.created == []
        assert "pruning only" in out

    def test_a_retried_run_does_not_double_create(self):
        """Cloud Scheduler retries a failed job; the description carries the
        date, so the second attempt must see it and skip."""
        fake = FakeClient(backups=[_backup("b1", "tier:biweekly 2026-09-16", "2026-09-16T05:00:00Z")])
        out = _run(fake, (9, 16), "--apply")
        assert fake.created == []
        assert "idempotent" in out

    def test_bootstrap_seeds_both_tiers(self):
        fake = FakeClient()
        _run(fake, (9, 10), "--apply", "--bootstrap")
        assert sorted(fake.created) == ["tier:biweekly 2026-09-10", "tier:monthly 2026-09-10"]


class TestPruning:
    def test_each_class_keeps_its_count_newest_first(self):
        fake = FakeClient(
            backups=[
                _backup("m2", "tier:monthly 2026-08-31", "2026-08-31T05:00:00Z"),
                _backup("m1", "tier:monthly 2026-07-31", "2026-07-31T05:00:00Z"),
                _backup("b2", "tier:biweekly 2026-09-01", "2026-09-01T05:00:00Z"),
                _backup("b1", "tier:biweekly 2026-08-16", "2026-08-16T05:00:00Z"),
                _backup("d4", "pre-deploy v1.23.0", "2026-09-01T10:00:00Z"),
                _backup("d3", "pre-deploy v1.22.2", "2026-08-30T10:00:00Z"),
                _backup("d2", "pre-deploy v1.22.1", "2026-08-29T10:00:00Z"),
                _backup("d1", "pre-deploy v1.22.0", "2026-08-28T10:00:00Z"),
            ]
        )
        _run(fake, (9, 10), "--apply")

        # keep newest monthly (m2), newest biweekly (b2), newest 3 manual (d4,d3,d2)
        assert sorted(fake.deleted) == ["b1", "d1", "m1"]

    def test_running_and_automated_backups_are_never_touched(self):
        fake = FakeClient(
            backups=[
                _backup("auto", "", "2026-09-09T05:00:00Z", btype="AUTOMATED"),
                _backup("inflight", "tier:biweekly 2026-09-10", "2026-09-10T05:00:00Z", status="RUNNING"),
                _backup("old-b", "tier:biweekly 2026-08-16", "2026-08-16T05:00:00Z"),
            ]
        )
        _run(fake, (9, 10), "--apply")
        # the RUNNING one is invisible to the prune, so old-b is still the
        # newest SUCCESSFUL biweekly and must be kept
        assert fake.deleted == []


class TestSafety:
    def test_dry_run_is_the_default_and_touches_nothing(self):
        fake = FakeClient(
            backups=[
                _backup("m2", "tier:monthly 2026-08-31", "2026-08-31T05:00:00Z"),
                _backup("m1", "tier:monthly 2026-07-31", "2026-07-31T05:00:00Z"),
            ]
        )
        out = _run(fake, (9, 16))  # biweekly day, deletable monthly — no --apply

        assert fake.created == []
        assert fake.deleted == []
        assert "[DRY RUN]" in out
        assert "Nothing was changed" in out

    def test_retention_drift_warns_but_does_not_patch(self):
        """Patching needs cloudsql.instances.update — instance-wide power a
        backup job must not hold. Warn and name the manual command instead."""
        fake = FakeClient(retained=3)
        out = _run(fake, (9, 10), "--apply")
        assert "retained-backups-count" in out
