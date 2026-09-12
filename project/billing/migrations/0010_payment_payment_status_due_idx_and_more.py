"""Payment/Expense idempotency constraints + the (payment_status, due_date) index.

The two unique constraints put a DATABASE guarantee behind idempotency rules that
until now existed only as a read-then-write check in Python — see the comments on
`Payment.Meta.constraints` and `Expense.Meta.constraints`.

`_assert_no_pending_duplicates` (payments) and `_assert_no_materialized_duplicates`
(expenses) run first so that a database which ALREADY contains double rows fails
with an actionable message naming them, rather than with a bare Postgres
constraint violation part-way through a deploy. Adding the constraint is what
surfaces such rows; it is not what created them.
"""

import django.db.models.functions.datetime
from django.core.management.base import CommandError
from django.db import migrations, models

PERIODIC = ("monthly", "quarterly")


def _assert_no_pending_duplicates(apps, schema_editor):
    Payment = apps.get_model("billing", "Payment")
    dupes = (
        Payment.objects.filter(payment_status="pending", payment_type__in=PERIODIC)
        .annotate(
            y=django.db.models.functions.datetime.ExtractYear("due_date"),
            m=django.db.models.functions.datetime.ExtractMonth("due_date"),
        )
        .values("student_id", "payment_type", "y", "m")
        .annotate(n=models.Count("id"))
        .filter(n__gt=1)
        .order_by("student_id", "y", "m")
    )
    rows = list(dupes[:20])
    if not rows:
        return

    detail = "\n".join(
        f"  student_id={r['student_id']} {r['payment_type']} due {r['y']}-{r['m']:02d}: {r['n']} pending payments"
        for r in rows
    )
    raise CommandError(
        "Cannot add unique_pending_periodic_payment_per_month: this database already "
        "has more than one PENDING periodic payment for the same student and due "
        f"month.\n{detail}\n"
        "These are double-billed rows. Inspect them, cancel the duplicates "
        "(`deactivate_payment`, or `manage.py reconcile_payment_schedule` — dry run "
        "by default), then re-run this migration."
    )


def _assert_no_materialized_duplicates(apps, schema_editor):
    """Same guard for `unique_materialized_expense_per_date` — the expense
    materialisers had the identical read-then-write race, so a DB from before
    v1.26 can carry two rows with one (generated_from, expense_date)."""
    Expense = apps.get_model("billing", "Expense")
    dupes = (
        Expense.objects.filter(generated_from__isnull=False)
        .values("generated_from_id", "expense_date")
        .annotate(n=models.Count("id"))
        .filter(n__gt=1)
        .order_by("generated_from_id", "expense_date")
    )
    rows = list(dupes[:20])
    if not rows:
        return

    detail = "\n".join(f"  generated_from={r['generated_from_id']} on {r['expense_date']}: {r['n']} rows" for r in rows)
    raise CommandError(
        "Cannot add unique_materialized_expense_per_date: this database already has "
        f"more than one materialised expense for the same template and date.\n{detail}\n"
        "These are duplicate recurring-expense rows. Delete the extras (keep one per "
        "template+date), then re-run this migration."
    )


def _noop(apps, schema_editor):
    """Reversing the constraint needs no data work."""


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0009_alter_enrollment_status_alter_payment_payment_status_and_more"),
        ("students", "0012_alter_group_options_alter_parent_options_and_more"),
    ]

    operations = [
        migrations.RunPython(_assert_no_pending_duplicates, _noop),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(fields=["payment_status", "due_date"], name="payment_status_due_idx"),
        ),
        migrations.RunPython(_assert_no_materialized_duplicates, _noop),
        migrations.AddConstraint(
            model_name="expense",
            constraint=models.UniqueConstraint(
                fields=("generated_from", "expense_date"),
                name="unique_materialized_expense_per_date",
                violation_error_message="Ese gasto recurrente ya se ha generado para esa fecha.",
            ),
        ),
        migrations.AddConstraint(
            model_name="payment",
            constraint=models.UniqueConstraint(
                models.F("student"),
                models.F("payment_type"),
                django.db.models.functions.datetime.ExtractYear("due_date"),
                django.db.models.functions.datetime.ExtractMonth("due_date"),
                condition=models.Q(
                    ("payment_status", "pending"),
                    ("payment_type__in", PERIODIC),
                ),
                name="unique_pending_periodic_payment_per_month",
                violation_error_message=(
                    "Ya existe un pago pendiente de ese tipo para ese alumno en ese mes. "
                    "Edita o cancela el pago existente en lugar de crear otro."
                ),
            ),
        ),
    ]
