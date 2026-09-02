"""
GCP cost service — actual Google Cloud spend, month by month.

Google Cloud exposes ACTUAL costs programmatically only through the BigQuery
billing export (the Cloud Billing API prices SKUs, it does not report spend),
so this service queries that export table over BigQuery's REST API with the
google-auth stack gspread already pulls in — no new dependencies.

Configuration (env → settings; all optional — unset means the service reports
itself unconfigured and every lookup returns None, so the UI shows "—"):

  GCP_BILLING_EXPORT_TABLE    full id of the standard billing export table:
                              "project.dataset.gcp_billing_export_v1_XXXXXX".
  GCP_BILLING_PROJECT_ID      project the query job is billed to; defaults to
                              the export table's own project.
  GCP_BILLING_PROJECT_FILTER  optional `project.id` filter for billing accounts
                              that carry more than one project.
  GCP_BILLING_SERVICE_ACCOUNT_JSON / _FILE
                              dedicated credential; falls back to the Google
                              Sheets service-account envs (usually the same SA),
                              then to Application Default Credentials (the
                              attached service account on the VM / Cloud Run).

Two kinds of figure, per the expenses design:
  - the CURRENT month is dynamic — read live from BigQuery (cached) and never
    persisted while the month is still running;
  - a FINISHED month is archived once as a plain `software` Expense row by
    `archive_month()` (Beat task / `manage.py archive_gcp_costs`), and from
    then on the saved row is the source of truth for every calculation.

Results are cached in the Django cache (the DB cache in testing/production) so
the QA dashboard and the expenses page cost one BigQuery query every few hours,
not one per page load. Failures are cached briefly too, so a broken export
cannot add a 15 s BigQuery timeout to every render. Never raises.
"""

from __future__ import annotations

import calendar
import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.core.cache import cache

from billing.models import Expense

logger = logging.getLogger(__name__)

# Marker for the archived rows — `archive_month` matches on this description, so
# an admin renaming the row detaches it from the automation (a fresh one would
# be created on the next run). The notes on each row say as much.
GCP_EXPENSE_DESCRIPTION = "Google Cloud Platform"

_BIGQUERY_SCOPE = "https://www.googleapis.com/auth/bigquery.readonly"

# The table id is interpolated into the SQL (BigQuery cannot parameterise table
# names), so even though it comes from our own env it is validated to the shape
# of a fully-qualified table id before it goes anywhere near a query.
_TABLE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_$-]+){2}$")

# Cached failure marker — distinguishes "BigQuery said no" (retry in a few
# minutes, don't hammer it per page load) from a plain cache miss.
_UNAVAILABLE = "unavailable"
_FAILURE_TTL = 10 * 60
_CURRENT_MONTH_TTL = 6 * 60 * 60  # the figure grows during the month
_PAST_MONTH_TTL = 24 * 60 * 60  # only late invoice adjustments move it

# The QA card and the expense concepts use Spanish month labels.
SPANISH_MONTH_ABBR = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"]
SPANISH_MONTHS = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]


def is_configured() -> bool:
    """True iff a billing-export table is configured (credentials are resolved lazily)."""
    return bool(getattr(settings, "GCP_BILLING_EXPORT_TABLE", ""))


def _load_service_account_info() -> dict | None:
    """Service-account dict from the dedicated envs, else the Sheets envs, else None."""
    for json_setting, file_setting in (
        ("GCP_BILLING_SERVICE_ACCOUNT_JSON", "GCP_BILLING_SERVICE_ACCOUNT_FILE"),
        ("GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON", "GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE"),
    ):
        inline = getattr(settings, json_setting, "")
        if inline:
            try:
                return json.loads(inline)
            except json.JSONDecodeError as e:
                logger.warning("%s is not valid JSON: %s", json_setting, e)
                continue
        path = getattr(settings, file_setting, "")
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("%s unreadable: %s", file_setting, e)
    return None


def _credentials():
    """Google credentials for BigQuery, or None when nothing resolves."""
    # Late imports — the google stack is only needed once a real query runs.
    info = _load_service_account_info()
    if info is not None:
        from google.oauth2.service_account import Credentials

        return Credentials.from_service_account_info(info, scopes=[_BIGQUERY_SCOPE])
    try:
        import google.auth

        creds, _project = google.auth.default(scopes=[_BIGQUERY_SCOPE])
        return creds
    except Exception:  # noqa: BLE001 — no ADC on a dev machine is the normal case
        return None


def _query_month(year: int, month: int) -> Decimal | None:
    """One BigQuery REST query: net cost (cost + credits) for the invoice month.

    Returns the amount in the billing account's currency, or None on any
    failure. `invoice.month` is the canonical month bucket of the export —
    usage is attributed to the invoice it lands on, which is what Google's own
    billing reports page shows.
    """
    table = getattr(settings, "GCP_BILLING_EXPORT_TABLE", "")
    if not _TABLE_ID_RE.match(table):
        # Deliberately not echoing the value: CodeQL taints it as sensitive
        # (py/clear-text-logging-sensitive-data) and the operator can read the
        # env var directly — the setting name is enough to locate the problem.
        logger.warning("GCP_BILLING_EXPORT_TABLE does not look like project.dataset.table; check the env var.")
        return None

    creds = _credentials()
    if creds is None:
        logger.warning("GCP billing export is configured but no Google credential resolved.")
        return None

    # BigQuery cannot parameterise a table name; the id is regex-validated above
    # and every value travels as a named query parameter.
    net_cost = "SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0))"
    sql = f"SELECT {net_cost} FROM `{table}` WHERE invoice.month = @invoice_month"  # nosec B608  # noqa: S608
    params: list[dict[str, Any]] = [
        {
            "name": "invoice_month",
            "parameterType": {"type": "STRING"},
            "parameterValue": {"value": f"{year:04d}{month:02d}"},
        }
    ]
    project_filter = getattr(settings, "GCP_BILLING_PROJECT_FILTER", "")
    if project_filter:
        sql += " AND project.id = @project_id"
        params.append(
            {
                "name": "project_id",
                "parameterType": {"type": "STRING"},
                "parameterValue": {"value": project_filter},
            }
        )

    job_project = getattr(settings, "GCP_BILLING_PROJECT_ID", "") or table.split(".", 1)[0]

    try:
        from google.auth.transport.requests import AuthorizedSession

        session = AuthorizedSession(creds)
        response = session.post(
            f"https://bigquery.googleapis.com/bigquery/v2/projects/{job_project}/queries",
            json={
                "query": sql,
                "useLegacySql": False,
                "parameterMode": "NAMED",
                "queryParameters": params,
                "timeoutMs": 15000,
                "maxResults": 2,
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("jobComplete"):
            logger.warning("BigQuery cost query did not complete within the timeout.")
            return None
        rows = data.get("rows") or []
        raw = rows[0]["f"][0]["v"] if rows else None
        # SUM over an empty month yields a NULL cell — an honest 0.00.
        amount = Decimal("0.00") if raw is None else Decimal(str(raw))
        return amount.quantize(Decimal("0.01"))
    except (InvalidOperation, KeyError, IndexError, ValueError):
        logger.exception("Unexpected BigQuery response shape for the GCP cost query")
        return None
    except Exception:  # noqa: BLE001 — network/auth/HTTP errors must never break a page
        logger.exception("GCP cost query failed")
        return None


def month_cost(year: int, month: int) -> Decimal | None:
    """Live GCP spend for (year, month), cached. None when unconfigured/unavailable."""
    if not is_configured():
        return None

    cache_key = f"gcp_cost:{year:04d}-{month:02d}"
    try:
        cached = cache.get(cache_key)
    except Exception:  # noqa: BLE001 — an unreachable cache must not take the page down
        logger.exception("Cache read failed for %s", cache_key)
        cached = None
    if cached == _UNAVAILABLE:
        return None
    if cached is not None:
        try:
            return Decimal(cached)
        except InvalidOperation:
            pass  # corrupted entry — fall through and re-query

    amount = _query_month(year, month)
    today = date.today()
    is_current = (year, month) == (today.year, today.month)
    try:
        if amount is None:
            cache.set(cache_key, _UNAVAILABLE, _FAILURE_TTL)
        else:
            cache.set(cache_key, str(amount), _CURRENT_MONTH_TTL if is_current else _PAST_MONTH_TTL)
    except Exception:  # noqa: BLE001
        logger.exception("Cache write failed for %s", cache_key)
    return amount


def archived_gcp_expense(year: int, month: int) -> Expense | None:
    """The saved GCP expense row for (year, month), if the month was archived."""
    return (
        Expense.objects.filter(
            is_recurring=False,
            description=GCP_EXPENSE_DESCRIPTION,
            expense_date__year=year,
            expense_date__month=month,
        )
        .order_by("id")
        .first()
    )


def archive_month(year: int, month: int) -> dict[str, Any]:
    """Persist a FINISHED month's GCP spend as a `software` Expense row.

    Idempotent — an already-archived month is left alone, so re-running the
    Beat task / management command (Cloud Run Jobs retry on failure) never
    double-books. A `cache.add()` slot guards the read-then-create against two
    overlapping runs, the same one-shot-claim pattern the rate limiter uses
    (`generated_from` is NULL here, so `unique_materialized_expense_per_date`
    cannot back this row up).

    The row is dated the LAST day of the month so it lands in that month's
    totals, and carries an auto-generated note naming its origin.
    """
    existing = archived_gcp_expense(year, month)
    if existing is not None:
        return {"status": "exists", "expense_id": existing.pk, "amount": str(existing.amount)}

    if not is_configured():
        return {"status": "unconfigured"}

    amount = month_cost(year, month)
    if amount is None:
        # BigQuery unavailable — report it and let the next run retry.
        return {"status": "unavailable"}
    if amount < Decimal("0.01"):
        # Expense.amount has MinValueValidator(0.01); a free month stores nothing.
        return {"status": "zero", "amount": str(amount)}

    lock_key = f"gcp_archive_lock:{year:04d}-{month:02d}"
    try:
        if not cache.add(lock_key, "1", 5 * 60):
            return {"status": "locked"}
    except Exception:  # noqa: BLE001 — a dead cache degrades to the .exists() check above
        logger.exception("Cache add failed for %s", lock_key)

    expense = Expense(
        description=GCP_EXPENSE_DESCRIPTION,
        category="software",
        amount=amount,
        expense_date=date(year, month, calendar.monthrange(year, month)[1]),
        notes=(
            f"Importe real facturado por Google Cloud en {SPANISH_MONTHS[month - 1]} {year}. "
            "Generado automáticamente al cierre del mes — no renombrar."
        ),
        is_recurring=False,
    )
    expense.full_clean()
    expense.save()
    logger.info("Archived GCP spend for %04d-%02d as expense %d (%s €)", year, month, expense.pk, amount)
    return {"status": "created", "expense_id": expense.pk, "amount": str(amount)}


def previous_month(today: date | None = None) -> tuple[int, int]:
    """(year, month) of the month before `today`."""
    today = today or date.today()
    last_of_previous = today.replace(day=1) - timedelta(days=1)
    return last_of_previous.year, last_of_previous.month


def qa_card_amounts(today: date | None = None) -> dict[str, Any]:
    """Figures for the QA dashboard's "Gastos GCP" line.

    The previous month prefers its ARCHIVED expense row (the saved value is the
    source of truth once a month closes) and only falls back to a live query;
    the current month is always live. Amounts are None when unavailable — the
    template renders those as "—".
    """
    today = today or date.today()
    prev_year, prev_month = previous_month(today)

    archived = archived_gcp_expense(prev_year, prev_month)
    previous_amount = archived.amount if archived is not None else month_cost(prev_year, prev_month)

    return {
        "previous": previous_amount,
        "previous_label": SPANISH_MONTH_ABBR[prev_month - 1],
        "current": month_cost(today.year, today.month),
        "current_label": SPANISH_MONTH_ABBR[today.month - 1],
    }


__all__ = [
    "GCP_EXPENSE_DESCRIPTION",
    "archive_month",
    "archived_gcp_expense",
    "is_configured",
    "month_cost",
    "previous_month",
    "qa_card_amounts",
]
