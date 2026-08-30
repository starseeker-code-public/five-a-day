"""
Provisioning for the EnrollmentType reference table.

EnrollmentType rows are reference data, not user data: `EnrollmentService._resolve_enrollment_type`
looks them up by `name` and raises when one is missing, so an environment with an empty
table cannot enroll anybody. Nothing in the schema creates them — `0001_initial` builds
the table only — so every environment needs this command run once. `entrypoint.sh` calls
it on boot for testing and production; QA seeding calls the same function so the two can
never drift.

Amounts come from SiteConfiguration, the single source of truth for pricing. Because an
EnrollmentType is a *matrícula* category, `base_amount_*` holds the one-time MATRÍCULA fee
for that category — the figures an admin edits under "Matrículas" in /management/ — not a
mensualidad. The matrícula does not vary with the schedule, so both columns carry the same
value; they are kept apart only because the schema has always had two.
"""

from decimal import Decimal

from billing.constants import ENROLLMENT_TYPE_DISPLAY_ES
from billing.models import EnrollmentType, SiteConfiguration

# The four categories `EnrollmentService._resolve_enrollment_type` can ask for. This is
# the complete set — every enrollment is exactly one of them.
REQUIRED_ENROLLMENT_TYPES = ("new_student", "returning_student", "adults", "special")

# Below this the DecimalField's MinValueValidator(0.01) rejects the row.
_MIN_AMOUNT = Decimal("0.01")


def _amounts_for(name: str, config: SiteConfiguration) -> tuple[Decimal, Decimal]:
    """Return the (full_time, part_time) matrícula fee for an enrollment category.

    Both columns get the same figure — a matrícula is charged per student, not per
    schedule. `EnrollmentService.compute_enrollment_fee` is the live calculation; these
    rows mirror it so the table an admin reads in /admin/ agrees with what is billed.
    """
    if name == "adults":
        # Adults pay their own, lower matrícula and are not eligible for the
        # returning-student discount.
        amount = config.adult_enrollment_fee
    elif name == "returning_student":
        # Re-enrolling in a later academic year takes a flat discount off the matrícula.
        discount = config.returning_student_enrollment_discount or Decimal("0.00")
        amount = config.children_enrollment_fee - discount
    elif name == "special":
        # A special enrollment is always priced by hand, so there is no meaningful
        # figure here; the minimum exists only to satisfy NOT NULL + MinValueValidator.
        amount = _MIN_AMOUNT
    else:
        amount = config.children_enrollment_fee
    return amount, amount


def ensure_enrollment_types(config: SiteConfiguration | None = None) -> dict[str, list[str]]:
    """
    Create any missing EnrollmentType rows and repair drifted labels/amounts.

    Idempotent: safe to run on every container start. Returns a report of what changed
    so callers can log it.
    """
    config = config or SiteConfiguration.get_config()
    report: dict[str, list[str]] = {"created": [], "updated": [], "unchanged": []}

    for name in REQUIRED_ENROLLMENT_TYPES:
        display = ENROLLMENT_TYPE_DISPLAY_ES.get(name, name)
        full_time, part_time = _amounts_for(name, config)
        full_time = max(full_time, _MIN_AMOUNT)
        part_time = max(part_time, _MIN_AMOUNT)

        enrollment_type, created = EnrollmentType.objects.get_or_create(
            name=name,
            defaults={
                "display_name": display,
                "base_amount_full_time": full_time,
                "base_amount_part_time": part_time,
            },
        )
        if created:
            report["created"].append(name)
            continue

        # Repair rows seeded before the labels were translated, or left behind by a
        # pricing change in SiteConfiguration. `active` and `description` are
        # deliberately untouched — an admin may have edited them on purpose.
        changed = []
        if enrollment_type.display_name != display:
            enrollment_type.display_name = display
            changed.append("display_name")
        if enrollment_type.base_amount_full_time != full_time:
            enrollment_type.base_amount_full_time = full_time
            changed.append("base_amount_full_time")
        if enrollment_type.base_amount_part_time != part_time:
            enrollment_type.base_amount_part_time = part_time
            changed.append("base_amount_part_time")

        if changed:
            enrollment_type.save(update_fields=[*changed, "updated_at"])
            report["updated"].append(f"{name} ({', '.join(changed)})")
        else:
            report["unchanged"].append(name)

    return report
