"""Unit tests for core.views.payments helper functions.

Pure-function tests for `parse_date_value` + direct-invocation test for the
unreferenced `payment_detail` AJAX helper (not URL-routed). HTTP-based
payment view tests live in integration/test_payment_views.py.
"""

import json
from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.test import RequestFactory

pytestmark = pytest.mark.django_db


# ============================================================================
# parse_date_value — pure function, no DB
# ============================================================================


class TestParseDateValue:
    def test_none_returns_none(self):
        from core.views.payments import parse_date_value

        assert parse_date_value(None) is None

    def test_empty_string_returns_none(self):
        from core.views.payments import parse_date_value

        assert parse_date_value("") is None
        assert parse_date_value("   ") is None

    def test_date_object_passes_through(self):
        from core.views.payments import parse_date_value

        d = date(2026, 5, 1)
        assert parse_date_value(d) == d

    def test_iso_format(self):
        from core.views.payments import parse_date_value

        assert parse_date_value("2026-05-01") == date(2026, 5, 1)

    def test_spanish_format(self):
        from core.views.payments import parse_date_value

        assert parse_date_value("01/05/2026") == date(2026, 5, 1)

    def test_invalid_format_raises(self):
        from core.views.payments import parse_date_value

        with pytest.raises(ValidationError, match="Formato de fecha"):
            parse_date_value("not-a-date")


# ============================================================================
# payment_detail (AJAX JSON) — unreferenced helper, called directly
# ============================================================================


class TestPaymentDetailAjax:
    def test_returns_full_payment_data(self, completed_payment):
        """Legacy AJAX JSON endpoint isn't URL-routed — call view function directly."""
        from core.views.payments import payment_detail

        rf = RequestFactory()
        req = rf.get("/")
        response = payment_detail(req, completed_payment.id)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["id"] == completed_payment.id
        assert data["amount"] == str(completed_payment.amount)
