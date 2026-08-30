"""
Provisioning for the EnrollmentType reference table.

EnrollmentType rows are reference data, not user data: `EnrollmentService._resolve_plan`
looks them up by `name` and raises when one is missing, so an environment with an empty
table cannot enroll anybody. Nothing in the schema creates them — `0001_initial` builds
the table only — so every environment needs this command run once. `entrypoint.sh` calls
it on boot for testing and production; QA seeding calls the same function so the two can
never drift.

Amounts come from SiteConfiguration, the single source of truth for pricing. They are only
a fallback: `EnrollmentService` computes `final_amount` up front, and `Enrollment.save()`
reads `base_amount_*` solely when `final_amount` was not supplied. They still have to be
right, because that fallback is what an admin-created enrollment is charged.
"""

from decimal import Decimal

from billing.constants import ENROLLMENT_TYPE_DISPLAY_ES
from billing.models import EnrollmentType, SiteConfiguration

# The four types `EnrollmentService._resolve_plan` can ask for. `half_month` and
# `languages_ticket` are valid choices with Spanish labels but are never resolved
# to a row — they are modelled as discounts, not as enrollment types.
REQUIRED_ENROLLMENT_TYPES = ("monthly", "quarterly", "adults", "special")

# Below this the DecimalField's MinValueValidator(0.01) rejects the row.
_MIN_AMOUNT = Decimal("0.01")


def _amounts_for(name: str, config: SiteConfiguration) -> tuple[Decimal, Decimal]:
    """Return (full_time, part_time) base amounts for an enrollment type."""
    if name == "quarterly":
        return config.full_time_monthly_fee * 3, config.part_time_monthly_fee * 3
    if name == "adults":
        # Adults are a single group rate — there is no part-time adult schedule.
        return config.adult_group_monthly_fee, config.adult_group_monthly_fee
    # `monthly` and `special` both fall back to the standard monthly fees. A special
    # enrollment always carries a manual amount, so its fallback is never the real
    # price; it exists to satisfy the NOT NULL + MinValueValidator constraints.
    return config.full_time_monthly_fee, config.part_time_monthly_fee


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
