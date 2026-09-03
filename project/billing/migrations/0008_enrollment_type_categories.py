"""Turn EnrollmentType into a matrícula category (new / returning / adult / special).

The table used to mix two unrelated ideas: `monthly` and `quarterly` describe a payment
cadence, which `Enrollment.payment_modality` already stores, while `adults` and `special`
describe who is being enrolled. This migration keeps only the second idea and re-points
every existing enrollment onto the category it actually belongs to, derived from the same
rules `EnrollmentService` applies at creation time.

`base_amount_*` changes meaning with it: it is now the one-time matrícula fee for the
category, taken from SiteConfiguration (the figures under "Matrículas" in /management/).
"""

from decimal import Decimal

from django.db import migrations, models

# SiteConfiguration defaults, repeated here so the migration stays self-contained when it
# runs against a database that has no configuration row yet.
DEFAULT_CHILDREN_FEE = Decimal("40.00")
DEFAULT_ADULT_FEE = Decimal("20.00")
DEFAULT_RETURNING_DISCOUNT = Decimal("20.00")
MIN_AMOUNT = Decimal("0.01")

NEW_TYPES = ("new_student", "returning_student", "adults", "special")

DISPLAY_ES = {
    "new_student": "Nuevo estudiante",
    "returning_student": "Antiguo estudiante",
    "adults": "Adulto",
    "special": "Especial",
}


def _amounts(config):
    children = getattr(config, "children_enrollment_fee", None) or DEFAULT_CHILDREN_FEE
    adult = getattr(config, "adult_enrollment_fee", None) or DEFAULT_ADULT_FEE
    discount = getattr(config, "returning_student_enrollment_discount", None) or DEFAULT_RETURNING_DISCOUNT
    return {
        "new_student": max(children, MIN_AMOUNT),
        "returning_student": max(children - discount, MIN_AMOUNT),
        "adults": max(adult, MIN_AMOUNT),
        "special": MIN_AMOUNT,
    }


def migrate_types(apps, schema_editor):
    EnrollmentType = apps.get_model("billing", "EnrollmentType")
    Enrollment = apps.get_model("billing", "Enrollment")
    SiteConfiguration = apps.get_model("billing", "SiteConfiguration")

    if not EnrollmentType.objects.exists():
        # A fresh database has no reference data — `0001_initial` inserts none and this
        # migration must not start, or `seed_enrollment_types` stops being the single
        # provisioning path and every test database arrives pre-seeded.
        return

    amounts = _amounts(SiteConfiguration.objects.first())

    rows = {}
    for name in NEW_TYPES:
        amount = amounts[name]
        rows[name], _ = EnrollmentType.objects.get_or_create(
            name=name,
            defaults={
                "display_name": DISPLAY_ES[name],
                "base_amount_full_time": amount,
                "base_amount_part_time": amount,
            },
        )
        # `adults` and `special` already existed and carry monthly amounts; bring every
        # row onto the new meaning so the table is internally consistent.
        EnrollmentType.objects.filter(pk=rows[name].pk).update(
            display_name=DISPLAY_ES[name],
            base_amount_full_time=amount,
            base_amount_part_time=amount,
        )

    # Re-point enrollments that sit on a retired type. `special` and `adults` keep their
    # rows, so only the cadence-named ones move; an adult on `monthly` is caught by
    # schedule_type, and everyone else splits on whether they have an earlier year.
    stale = Enrollment.objects.exclude(enrollment_type__name__in=NEW_TYPES).select_related("enrollment_type")
    for enrollment in stale:
        if enrollment.schedule_type == "adult_group":
            target = "adults"
        elif (
            Enrollment.objects.filter(student_id=enrollment.student_id)
            .exclude(academic_year=enrollment.academic_year)
            .exists()
        ):
            target = "returning_student"
        else:
            target = "new_student"
        Enrollment.objects.filter(pk=enrollment.pk).update(enrollment_type=rows[target])

    EnrollmentType.objects.exclude(name__in=NEW_TYPES).delete()


def unmigrate_types(apps, schema_editor):
    """Recreate a `monthly` row and park every enrollment on it.

    The cadence a pre-migration row encoded is `Enrollment.payment_modality`, which was
    never lost, so nothing needs recovering — this only restores a shape the old code can
    load.
    """
    EnrollmentType = apps.get_model("billing", "EnrollmentType")
    Enrollment = apps.get_model("billing", "Enrollment")
    SiteConfiguration = apps.get_model("billing", "SiteConfiguration")

    config = SiteConfiguration.objects.first()
    full_time = getattr(config, "full_time_monthly_fee", None) or Decimal("54.00")
    part_time = getattr(config, "part_time_monthly_fee", None) or Decimal("36.00")

    monthly, _ = EnrollmentType.objects.get_or_create(
        name="monthly",
        defaults={
            "display_name": "Mensual",
            "base_amount_full_time": full_time,
            "base_amount_part_time": part_time,
        },
    )
    Enrollment.objects.filter(enrollment_type__name__in=("new_student", "returning_student")).update(
        enrollment_type=monthly
    )
    EnrollmentType.objects.filter(name__in=("new_student", "returning_student")).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0007_alter_enrollment_options_and_more"),
        # `audit_logs` must exist before this runs: the audit receivers are
        # connected during migrate, and any future `enrollment.save()` added to
        # `migrate_types` would try to INSERT an AuditLog row. Today every write
        # here is a queryset `.update()` (which emits no signals), so it is safe
        # by accident of style — this dependency makes it safe by construction.
        ("core", "0004_add_audit_log"),
    ]

    operations = [
        migrations.AlterField(
            model_name="enrollmenttype",
            name="name",
            field=models.CharField(
                choices=[
                    ("new_student", "New Student"),
                    ("returning_student", "Returning Student"),
                    ("adults", "Adults"),
                    ("special", "Special"),
                ],
                max_length=20,
                unique=True,
            ),
        ),
        migrations.RunPython(migrate_types, unmigrate_types),
    ]
