"""Unit tests for core.views.payments helper functions.

Pure-function tests for `parse_date_value`. HTTP-based payment view tests live
in integration/test_payment_views.py.
"""

from datetime import date

import pytest
from django.core.exceptions import ValidationError

from core.views.payments import parse_date_value

pytestmark = pytest.mark.django_db


# ============================================================================
# parse_date_value — pure function, no DB
# ============================================================================


class TestParseDateValue:
    def test_none_returns_none(self):
        assert parse_date_value(None) is None

    def test_empty_string_returns_none(self):
        assert parse_date_value("") is None
        assert parse_date_value("   ") is None

    def test_date_object_passes_through(self):
        d = date(2026, 5, 1)
        assert parse_date_value(d) == d

    def test_iso_format(self):
        assert parse_date_value("2026-05-01") == date(2026, 5, 1)

    def test_spanish_format(self):
        assert parse_date_value("01/05/2026") == date(2026, 5, 1)

    def test_invalid_format_raises(self):
        with pytest.raises(ValidationError, match="Formato de fecha"):
            parse_date_value("not-a-date")
