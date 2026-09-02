"""Tiered retention for the production Cloud SQL backups.

The Python port of ``scripts/backup_retention.sh``, existing so the policy can
finally be SCHEDULED: the bash script needs a human with gcloud, and for months
the docs said "schedule daily" while nothing did — so the biweekly and monthly
recovery points it describes did not exist. This command runs as the
``fiveaday-backup-retention`` Cloud Run Job (Cloud Scheduler, daily), exactly
like every other cron in this project.

THE POLICY (same as the shell script)
    daily     7  AUTOMATED — managed natively by Cloud SQL. This command only
                 WARNS on drift: patching it needs ``cloudsql.instances.update``,
                 which is instance-wide and far broader than a backup job should
                 hold. The deploy preflight asserts the same config anyway.
    biweekly  1  ON_DEMAND "tier:biweekly", created on the 1st and the 16th
    monthly   1  ON_DEMAND "tier:monthly",  created on the last day of the month
    manual    3  ON_DEMAND anything else (deploy backups), newest kept

Tiers cannot be created retroactively — a point only exists if this ran that
day. ``--bootstrap`` seeds both tiers today on first use.

Talks to the SQL Admin REST API with google-auth + httpx (both already
dependencies) rather than google-api-python-client, which would be a new
dependency for four endpoints. On Cloud Run the credentials come from the
job's service account; locally, from ``gcloud auth application-default``.

DRY RUN BY DEFAULT, like ``reconcile_payment_schedule``. The scheduler passes
``--apply``. Day-of-month decisions use ``timezone.localdate()`` — the container
clock is UTC, and around midnight UTC "the 1st" is still the 31st in Madrid,
which is exactly the class of bug the birthday task had.
"""

import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

logger = logging.getLogger(__name__)

PROJECT = "five-a-day-evolution"
INSTANCE = "fiveaday-db"

KEEP_DAILY_AUTOMATED = 7
KEEP_BIWEEKLY = 1
KEEP_MONTHLY = 1
KEEP_MANUAL = 3

_API = "https://sqladmin.googleapis.com/v1"
_SCOPE = "https://www.googleapis.com/auth/sqlservice.admin"


class SqlAdminClient:
    """Thin wrapper over the four SQL Admin endpoints the policy needs.

    A class (not module functions) so the tests can hand the command a fake and
    exercise the policy without credentials or network.
    """

    def __init__(self):
        import google.auth
        from google.auth.transport.requests import Request

        self._credentials, _ = google.auth.default(scopes=[_SCOPE])
        self._auth_request = Request()

    def _headers(self):
        if not self._credentials.valid:
            self._credentials.refresh(self._auth_request)
        return {"Authorization": f"Bearer {self._credentials.token}"}

    def _call(self, method: str, url: str, **kwargs):
        import httpx

        response = httpx.request(method, url, headers=self._headers(), timeout=30.0, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    def instance(self) -> dict:
        return self._call("GET", f"{_API}/projects/{PROJECT}/instances/{INSTANCE}")

    def list_backups(self) -> list[dict]:
        items, token = [], None
        while True:
            params = {"maxResults": 100}
            if token:
                params["pageToken"] = token
            page = self._call("GET", f"{_API}/projects/{PROJECT}/instances/{INSTANCE}/backupRuns", params=params)
            items.extend(page.get("items", []))
            token = page.get("nextPageToken")
            if not token:
                return items

    def create_backup(self, description: str) -> dict:
        return self._call(
            "POST",
            f"{_API}/projects/{PROJECT}/instances/{INSTANCE}/backupRuns",
            json={"description": description},
        )

    def delete_backup(self, backup_id: str) -> dict:
        return self._call("DELETE", f"{_API}/projects/{PROJECT}/instances/{INSTANCE}/backupRuns/{backup_id}")

    def operation(self, name: str) -> dict:
        return self._call("GET", f"{_API}/projects/{PROJECT}/operations/{name}")


def classify(description: str) -> str:
    if description.startswith("tier:biweekly"):
        return "biweekly"
    if description.startswith("tier:monthly"):
        return "monthly"
    return "manual"


class Command(BaseCommand):
    help = "Tiered Cloud SQL backup retention (biweekly/monthly points + prune). Dry run unless --apply."

    #: Overridable in tests.
    client_class = SqlAdminClient

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write changes. Without it, nothing is touched.")
        parser.add_argument("--bootstrap", action="store_true", help="Seed both tiers today (first run).")

    def handle(self, *args, **options):
        self.apply = options["apply"]
        self.client = self.client_class()

        today = timezone.localdate()
        is_biweekly_day = today.day in (1, 16)
        is_month_end = (today + timezone.timedelta(days=1)).day == 1

        prefix = "" if self.apply else "[DRY RUN] "
        self.stdout.write(f"{prefix}backup retention — {PROJECT}/{INSTANCE} — {today}")

        # 1. Automated retention: warn on drift, never patch (see module docstring).
        settings_ = self.client.instance().get("settings", {})
        retained = settings_.get("backupConfiguration", {}).get("backupRetentionSettings", {}).get("retainedBackups")
        if retained != KEEP_DAILY_AUTOMATED:
            self.stderr.write(
                self.style.WARNING(
                    f"automated retention is {retained}, policy wants {KEEP_DAILY_AUTOMATED} — "
                    f"fix by hand: gcloud sql instances patch {INSTANCE} "
                    f"--retained-backups-count={KEEP_DAILY_AUTOMATED}"
                )
            )

        # 2. Create tier points when the calendar says so. Skipped when an
        #    identical description already exists, so a retried job run (Cloud
        #    Scheduler retries on failure) cannot double-create.
        existing = {b.get("description", "") for b in self.client.list_backups()}
        wanted = []
        if options["bootstrap"]:
            wanted = ["biweekly", "monthly"]
        else:
            if is_biweekly_day:
                wanted.append("biweekly")
            if is_month_end:
                wanted.append("monthly")
            if not wanted:
                self.stdout.write("not a tier day (1st, 16th, or month end) — pruning only")

        for tier in wanted:
            description = f"tier:{tier} {today.isoformat()}"
            if description in existing:
                self.stdout.write(f"  {description} already exists — skipping (idempotent)")
                continue
            self.stdout.write(f"  + creating {description}")
            if self.apply:
                op = self.client.create_backup(description)
                self._wait(op)

        # 3. Prune each class to its keep-count, newest first. SUCCESSFUL only:
        #    an in-flight RUNNING backup is neither a keeper nor deletable.
        backups = [
            b for b in self.client.list_backups() if b.get("status") == "SUCCESSFUL" and b.get("type") == "ON_DEMAND"
        ]
        backups.sort(key=lambda b: b.get("windowStartTime", ""), reverse=True)

        deleted = 0
        for klass, keep in (("monthly", KEEP_MONTHLY), ("biweekly", KEEP_BIWEEKLY), ("manual", KEEP_MANUAL)):
            seen = 0
            self.stdout.write(f"--- {klass} (keep {keep}) ---")
            for backup in backups:
                if classify(backup.get("description", "")) != klass:
                    continue
                seen += 1
                label = f"{backup['id']}  {backup.get('windowStartTime', '?')}  {backup.get('description', '')!r}"
                if seen <= keep:
                    self.stdout.write(f"    KEEP   {label}")
                else:
                    self.stdout.write(f"    DELETE {label}")
                    if self.apply:
                        self.client.delete_backup(backup["id"])
                    deleted += 1
            if seen == 0:
                self.stdout.write("    (none yet)")

        self.stdout.write(f"{prefix}done — {deleted} backup(s) pruned")
        if not self.apply:
            self.stdout.write("Nothing was changed. Re-run with --apply to enforce the policy.")

    def _wait(self, operation: dict, timeout_seconds: int = 1200):
        """Block until the create finishes; a backup that never turns SUCCESSFUL
        must fail the job loudly, not silently leave a tier missing."""
        name = operation.get("name")
        if not name:
            raise CommandError(f"backup create returned no operation name: {operation}")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            op = self.client.operation(name)
            if op.get("status") == "DONE":
                if op.get("error"):
                    raise CommandError(f"backup operation failed: {op['error']}")
                return
            time.sleep(10)
        raise CommandError(f"backup operation {name} did not finish within {timeout_seconds}s")
