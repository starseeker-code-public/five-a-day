"""Unit tests for billing/services/gcp_cost_service.py — GCP spend lookups + archive.

The service is network-facing (BigQuery REST), so every test stubs either
`_query_month` (the network boundary) or the transport itself. No test here
ever talks to Google.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from billing.models import Expense
from billing.services import gcp_cost_service as svc

CONFIGURED = override_settings(GCP_BILLING_EXPORT_TABLE="my-project.billing.gcp_billing_export_v1_ABC")
# The local .env may legitimately set GCP_BILLING_EXPORT_TABLE (it is pre-set in
# all three shipped env files), so "unconfigured" must be forced, never assumed.
UNCONFIGURED = override_settings(GCP_BILLING_EXPORT_TABLE="")


@pytest.fixture(autouse=True)
def _clean_cache():
    """month_cost caches per (year, month) — isolate every test."""
    cache.clear()
    yield
    cache.clear()


# ============================================================================
# month_cost — configuration gate + caching
# ============================================================================


class TestMonthCost:
    @UNCONFIGURED
    def test_unconfigured_returns_none_without_querying(self):
        with patch.object(svc, "_query_month") as query:
            assert svc.month_cost(2026, 8) is None
        query.assert_not_called()

    @CONFIGURED
    def test_queries_once_and_caches(self):
        with patch.object(svc, "_query_month", return_value=Decimal("10.15")) as query:
            assert svc.month_cost(2026, 8) == Decimal("10.15")
            assert svc.month_cost(2026, 8) == Decimal("10.15")
        query.assert_called_once_with(2026, 8)

    @CONFIGURED
    def test_failure_is_cached_briefly(self):
        """A failed query must not re-hit BigQuery on every page render."""
        with patch.object(svc, "_query_month", return_value=None) as query:
            assert svc.month_cost(2026, 8) is None
            assert svc.month_cost(2026, 8) is None
        query.assert_called_once()


# ============================================================================
# _load_service_account_info + _credentials — credential resolution chain
# ============================================================================


class TestCredentialResolution:
    @override_settings(GCP_BILLING_SERVICE_ACCOUNT_JSON='{"type": "service_account", "project_id": "p"}')
    def test_dedicated_inline_json_wins(self):
        assert svc._load_service_account_info() == {"type": "service_account", "project_id": "p"}

    @override_settings(
        GCP_BILLING_SERVICE_ACCOUNT_JSON="{not json",
        GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON='{"type": "service_account", "project_id": "sheets"}',
    )
    def test_malformed_dedicated_json_falls_back_to_sheets(self):
        assert svc._load_service_account_info() == {"type": "service_account", "project_id": "sheets"}

    def test_service_account_file_is_read(self, tmp_path):
        cred_file = tmp_path / "sa.json"
        cred_file.write_text('{"type": "service_account", "project_id": "file"}', encoding="utf-8")
        with override_settings(GCP_BILLING_SERVICE_ACCOUNT_FILE=str(cred_file)):
            assert svc._load_service_account_info() == {"type": "service_account", "project_id": "file"}

    def test_unreadable_file_yields_none(self, tmp_path):
        with override_settings(GCP_BILLING_SERVICE_ACCOUNT_FILE=str(tmp_path / "missing.json")):
            assert svc._load_service_account_info() is None

    def test_nothing_configured_yields_none(self):
        assert svc._load_service_account_info() is None

    @override_settings(GCP_BILLING_SERVICE_ACCOUNT_JSON='{"type": "service_account"}')
    def test_credentials_built_from_service_account_info(self):
        with patch("google.oauth2.service_account.Credentials.from_service_account_info") as build:
            creds = svc._credentials()
        assert creds is build.return_value
        assert build.call_args.args[0] == {"type": "service_account"}

    def test_credentials_fall_back_to_adc(self):
        with patch("google.auth.default", return_value=("adc-creds", "proj")):
            assert svc._credentials() == "adc-creds"

    def test_no_adc_yields_none(self):
        with patch("google.auth.default", side_effect=OSError("no ADC")):
            assert svc._credentials() is None


# ============================================================================
# _query_month — table validation + response parsing
# ============================================================================


def _bq_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class TestQueryMonth:
    @override_settings(GCP_BILLING_EXPORT_TABLE="not a table id; DROP")
    def test_rejects_malformed_table_id(self):
        with patch.object(svc, "_credentials") as creds:
            assert svc._query_month(2026, 8) is None
        creds.assert_not_called()

    @CONFIGURED
    def test_no_credentials_returns_none(self):
        with patch.object(svc, "_credentials", return_value=None):
            assert svc._query_month(2026, 8) is None

    @CONFIGURED
    def test_parses_amount_and_quantizes(self):
        session = MagicMock()
        session.post.return_value = _bq_response({"jobComplete": True, "rows": [{"f": [{"v": "10.152345"}]}]})
        with (
            patch.object(svc, "_credentials", return_value=object()),
            patch("google.auth.transport.requests.AuthorizedSession", return_value=session),
        ):
            assert svc._query_month(2026, 8) == Decimal("10.15")
        # The invoice month rides as a named parameter, never interpolated.
        body = session.post.call_args.kwargs["json"]
        assert body["queryParameters"][0]["parameterValue"]["value"] == "202608"

    @CONFIGURED
    def test_null_sum_means_zero(self):
        session = MagicMock()
        session.post.return_value = _bq_response({"jobComplete": True, "rows": [{"f": [{"v": None}]}]})
        with (
            patch.object(svc, "_credentials", return_value=object()),
            patch("google.auth.transport.requests.AuthorizedSession", return_value=session),
        ):
            assert svc._query_month(2026, 8) == Decimal("0.00")

    @CONFIGURED
    def test_incomplete_job_returns_none(self):
        session = MagicMock()
        session.post.return_value = _bq_response({"jobComplete": False})
        with (
            patch.object(svc, "_credentials", return_value=object()),
            patch("google.auth.transport.requests.AuthorizedSession", return_value=session),
        ):
            assert svc._query_month(2026, 8) is None

    @CONFIGURED
    def test_transport_error_returns_none(self):
        session = MagicMock()
        session.post.side_effect = OSError("boom")
        with (
            patch.object(svc, "_credentials", return_value=object()),
            patch("google.auth.transport.requests.AuthorizedSession", return_value=session),
        ):
            assert svc._query_month(2026, 8) is None


# ============================================================================
# archive_month — the "finished month becomes a saved Expense row" step
# ============================================================================


@pytest.mark.django_db
class TestArchiveMonth:
    @CONFIGURED
    def test_creates_software_expense_on_last_day(self):
        with patch.object(svc, "month_cost", return_value=Decimal("10.15")):
            result = svc.archive_month(2026, 8)

        assert result["status"] == "created"
        expense = Expense.objects.get(pk=result["expense_id"])
        assert expense.description == svc.GCP_EXPENSE_DESCRIPTION
        assert expense.category == "software"
        assert expense.amount == Decimal("10.15")
        assert expense.expense_date == date(2026, 8, 31)
        assert expense.is_recurring is False

    @CONFIGURED
    def test_idempotent(self):
        with patch.object(svc, "month_cost", return_value=Decimal("10.15")) as cost:
            first = svc.archive_month(2026, 8)
            second = svc.archive_month(2026, 8)

        assert first["status"] == "created"
        assert second["status"] == "exists"
        assert second["expense_id"] == first["expense_id"]
        cost.assert_called_once()
        assert Expense.objects.filter(description=svc.GCP_EXPENSE_DESCRIPTION).count() == 1

    @CONFIGURED
    def test_zero_month_stores_nothing(self):
        """Expense.amount requires >= 0.01 — a free month must not create a row."""
        with patch.object(svc, "month_cost", return_value=Decimal("0.00")):
            assert svc.archive_month(2026, 8)["status"] == "zero"
        assert not Expense.objects.exists()

    @CONFIGURED
    def test_unavailable_backend_stores_nothing(self):
        with patch.object(svc, "month_cost", return_value=None):
            assert svc.archive_month(2026, 8)["status"] == "unavailable"
        assert not Expense.objects.exists()

    @UNCONFIGURED
    def test_unconfigured(self):
        assert svc.archive_month(2026, 8)["status"] == "unconfigured"


# ============================================================================
# previous_month + qa_card_amounts
# ============================================================================


class TestPreviousMonth:
    def test_mid_year(self):
        assert svc.previous_month(date(2026, 9, 2)) == (2026, 8)

    def test_january_rolls_into_previous_year(self):
        assert svc.previous_month(date(2026, 1, 10)) == (2025, 12)


@pytest.mark.django_db
class TestQaCardAmounts:
    @CONFIGURED
    def test_prefers_archived_row_for_previous_month(self):
        Expense.objects.create(
            description=svc.GCP_EXPENSE_DESCRIPTION,
            category="software",
            amount=Decimal("10.15"),
            expense_date=date(2026, 8, 31),
        )
        with patch.object(svc, "month_cost", return_value=Decimal("2.23")) as cost:
            card = svc.qa_card_amounts(today=date(2026, 9, 2))

        assert card == {
            "previous": Decimal("10.15"),
            "previous_label": "ago",
            "current": Decimal("2.23"),
            "current_label": "sept",
        }
        # The archived row answered for August — only September was queried.
        cost.assert_called_once_with(2026, 9)

    @UNCONFIGURED
    def test_unconfigured_yields_placeholders(self):
        card = svc.qa_card_amounts(today=date(2026, 9, 2))
        assert card["previous"] is None
        assert card["current"] is None
        assert card["previous_label"] == "ago"
        assert card["current_label"] == "sept"


# ============================================================================
# manage.py archive_gcp_costs — the Cloud Scheduler wrapper
# ============================================================================


@pytest.mark.django_db
class TestArchiveGcpCostsCommand:
    def test_defaults_to_previous_month(self):
        with patch.object(svc, "archive_month", return_value={"status": "unconfigured"}) as archive:
            call_command("archive_gcp_costs")
        year, month = svc.previous_month()
        archive.assert_called_once_with(year, month)

    def test_explicit_month_and_year(self):
        with patch.object(svc, "archive_month", return_value={"status": "exists", "expense_id": 1}) as archive:
            call_command("archive_gcp_costs", month=8, year=2026)
        archive.assert_called_once_with(2026, 8)

    def test_month_without_year_rejected(self):
        with pytest.raises(CommandError):
            call_command("archive_gcp_costs", month=8)

    def test_unavailable_backend_fails_the_run(self):
        """Cloud Run Jobs retry on failure — an unreachable export must exit non-zero."""
        with (
            patch.object(svc, "archive_month", return_value={"status": "unavailable"}),
            pytest.raises(CommandError),
        ):
            call_command("archive_gcp_costs")
