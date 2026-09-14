"""Drop five SiteConfiguration pricing columns that nothing ever read (v1.29.5).

`old_student_discount`, `full_year_bonus`, `half_month_discount`,
`one_week_discount` and `three_week_discount` were defined and seeded in
`0001_initial` and consumed by NO service, view, task, template, form, admin or
JS file. `update_site_config` had already been made to refuse writing them,
because `old_student_discount` is visually the twin of the LIVE
`returning_student_enrollment_discount` and a crafted payload could persist a
value nothing would ever apply — a price sitting in the database that looks
authoritative and is not.

Verified dead before removal: a whole-repo search across every file type found
them only in this app's `models.py`/`constants.py`, in `0001_initial`, in the
`update_site_config` comment explaining the exclusion, in two tests asserting
they are ignored, and in the docs. `SiteConfigurationAdmin` deliberately
declares no `fieldsets`, so no admin page names them either, and nothing reads
the config dynamically (`model_to_dict`, `values()`, `getattr` by name).

**This migration is DESTRUCTIVE** — `deploy-production.yml`'s pre-mutation gate
matches `RemoveField` and will refuse the release without the `ack_destructive`
dispatch input. Use `ack_destructive`, NOT `force`: `force` also skips the QA
sign-off, the provenance gate and the version compare.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0016_siteconfiguration_part_time_child_monthly_fee_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="siteconfiguration",
            name="full_year_bonus",
        ),
        migrations.RemoveField(
            model_name="siteconfiguration",
            name="half_month_discount",
        ),
        migrations.RemoveField(
            model_name="siteconfiguration",
            name="old_student_discount",
        ),
        migrations.RemoveField(
            model_name="siteconfiguration",
            name="one_week_discount",
        ),
        migrations.RemoveField(
            model_name="siteconfiguration",
            name="three_week_discount",
        ),
    ]
