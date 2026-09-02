"""
Billing Celery tasks (v1.4).

Wraps existing management commands / service methods so they can be
scheduled via Celery Beat instead of an external cron.
"""

from datetime import date

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(name="billing.tasks.materialize_recurring_expenses_task", bind=True)
def materialize_recurring_expenses_task(self, month: int | None = None, year: int | None = None):
    """
    Monthly (day 1) job: turn every `is_recurring=True` Expense template into a
    concrete Expense row for the given month. Idempotent — skips templates that
    have already been materialised via `generated_from`.
    """
    from billing.services.expense_service import materialize_recurring

    today = date.today()
    m = month or today.month
    y = year or today.year
    created = materialize_recurring(m, y)
    logger.info("Materialized %d monthly recurring expenses for %02d/%d", created, m, y)
    return {"status": "success", "created": created, "month": m, "year": y}


@shared_task(name="billing.tasks.materialize_recurring_expenses_daily_task", bind=True)
def materialize_recurring_expenses_daily_task(self, target_date: str | None = None):
    """
    Daily job: materialise WEEKLY recurring templates whose weekday matches today
    and YEARLY templates whose month+day match today. Idempotent — safe to re-run
    (matches on template + concrete date). `target_date` is an ISO string for tests.
    """
    from billing.services.expense_service import materialize_recurring_for_date

    d = date.fromisoformat(target_date) if target_date else date.today()
    created = materialize_recurring_for_date(d)
    logger.info("Materialized %d weekly/yearly recurring expenses for %s", created, d.isoformat())
    return {"status": "success", "created": created, "date": d.isoformat()}


@shared_task(name="billing.tasks.archive_gcp_costs_task", bind=True)
def archive_gcp_costs_task(self, month: int | None = None, year: int | None = None):
    """
    Monthly (day 3) job: store the PREVIOUS month's real Google Cloud spend as a
    concrete `software` Expense row, so the finished month's figure is a saved
    value while the running month stays dynamic (read live on the expenses page).

    Runs on the 3rd, not the 1st, because the BigQuery billing export lags up to
    ~2 days behind usage — on the 1st the closed month is still incomplete.
    Idempotent — an already-archived month is skipped (see
    `gcp_cost_service.archive_month`). `month`/`year` override the target for
    backfills; by default the month before today is archived.
    """
    from billing.services.gcp_cost_service import archive_month, previous_month

    if month is None or year is None:
        year, month = previous_month()
    result = archive_month(year, month)
    logger.info("GCP cost archive for %02d/%d: %s", month, year, result)
    return {"month": month, "year": year, **result}


@shared_task(name="billing.tasks.generate_monthly_payments_task", bind=True)
def generate_monthly_payments_task(self, month: int | None = None, year: int | None = None):
    """
    Monthly (day 1) job: generate the pending monthly / quarterly payment
    rows for every active enrollment. Runs the existing `generate_payments`
    management command so CLI and Beat share exactly one code path.
    """
    import io

    from django.core.management import call_command

    today = date.today()
    m = month or today.month
    y = year or today.year

    logger.info("Generating monthly payments for %02d/%d", m, y)
    out = io.StringIO()
    call_command("generate_payments", month=m, year=y, stdout=out)
    log_output = out.getvalue()
    logger.info("generate_payments output:\n%s", log_output)
    return {"status": "success", "month": m, "year": y, "output": log_output}
