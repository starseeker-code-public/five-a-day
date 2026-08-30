"""
Idempotently provision the EnrollmentType reference table.

Runs on container start for testing and production (see entrypoint.sh). Without it
`EnrollmentService._resolve_enrollment_type` raises "EnrollmentType '<name>' not found" and no
student can be enrolled — the failure mode production shipped with until v1.17.1.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from billing.services.enrollment_type_service import ensure_enrollment_types


class Command(BaseCommand):
    help = "Create the EnrollmentType rows required for enrollment, reading amounts from SiteConfiguration."

    @transaction.atomic
    def handle(self, *args, **options):
        report = ensure_enrollment_types()

        for name in report["created"]:
            self.stdout.write(self.style.SUCCESS(f"  created  {name}"))
        for entry in report["updated"]:
            self.stdout.write(self.style.WARNING(f"  updated  {entry}"))
        for name in report["unchanged"]:
            self.stdout.write(f"  ok       {name}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Enrollment types ready: {len(report['created'])} created, "
                f"{len(report['updated'])} updated, {len(report['unchanged'])} unchanged."
            )
        )
