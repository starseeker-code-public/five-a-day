"""The "infantil" price band (`schedule_type="part_time_child"`).

Same one-class-a-week timetable as `part_time`, at its own cheaper rate for the
youngest children. It is a SCHEDULE TYPE, not a discount, so it has to behave
like the other bands everywhere: priced off `SiteConfiguration`, editable from
/management/, quarterly-derivable, and with every discount (hermano, cheque
idioma, junio) layering on top of it exactly as on full time.

It is deliberately NOT validated against `Student.is_adult` — an adult resolves
to `adult_group` before the plan is read at all, and the academy picks this band
by hand in a handful of cases a year.
"""

from decimal import Decimal

import pytest

from billing.constants import PART_TIME_CHILD_MONTHLY_FEE, SCHEDULE_TYPE_CHOICES
from billing.forms import ENROLLMENT_PLAN_CHOICES
from billing.money import monthly_fee_for, period_base_amount, quarterly_price_from_monthly
from billing.services.enrollment_service import EnrollmentService

pytestmark = pytest.mark.django_db


class TestItIsAFirstClassScheduleType:
    def test_the_choice_exists_and_is_priced_at_thirty_two_by_default(self, site_config):
        assert ("part_time_child", "Infantil (1 día/semana)") in SCHEDULE_TYPE_CHOICES
        assert PART_TIME_CHILD_MONTHLY_FEE == Decimal("32.00")
        assert site_config.part_time_child_monthly_fee == Decimal("32.00")

    def test_the_plan_choice_exists(self):
        assert "monthly_part_child" in dict(ENROLLMENT_PLAN_CHOICES)

    def test_the_money_layer_reads_its_own_column(self, site_config):
        """`monthly_fee_for` falls back to full time for an UNKNOWN schedule type,
        so a missing entry in `_SCHEDULE_FEE_ATTR` would bill 54 € silently."""
        assert monthly_fee_for("part_time_child", site_config) == site_config.part_time_child_monthly_fee
        assert period_base_amount(site_config, "part_time_child", "monthly") == Decimal("32.00")
        assert period_base_amount(site_config, "part_time_child", "quarterly") == quarterly_price_from_monthly(
            site_config.part_time_child_monthly_fee, site_config
        )


class TestTheServiceResolvesThePlan:
    def _plan(self, config, **data):
        return EnrollmentService._resolve_plan(config, data, is_adult=False, is_special=False, manual_amount=None)

    def test_it_resolves_to_its_own_band(self, site_config):
        base, schedule, modality = self._plan(site_config, enrollment_plan="monthly_part_child")
        assert (base, schedule, modality) == (site_config.part_time_child_monthly_fee, "part_time_child", "monthly")

    def test_it_does_not_disturb_the_ordinary_part_time_band(self, site_config):
        base, schedule, _ = self._plan(site_config, enrollment_plan="monthly_part")
        assert (base, schedule) == (site_config.part_time_monthly_fee, "part_time")

    def test_a_hand_priced_one_keeps_the_band_and_the_agreed_figure(self, site_config):
        base, schedule, modality = EnrollmentService._resolve_plan(
            site_config,
            {"enrollment_plan": "monthly_part_child"},
            is_adult=False,
            is_special=True,
            manual_amount=Decimal("25.00"),
        )
        assert (base, schedule, modality) == (Decimal("25.00"), "part_time_child", "monthly")

    def test_an_adult_still_wins_over_the_plan_widget(self, site_config):
        _, schedule, _ = EnrollmentService._resolve_plan(
            site_config,
            {"enrollment_plan": "monthly_part_child"},
            is_adult=True,
            is_special=False,
            manual_amount=None,
        )
        assert schedule == "adult_group"


class TestDiscountsLayerOnTopOfIt:
    def test_the_cheque_idioma_comes_off_it(self, site_config):
        """Every discount predicate in billing asks `schedule_type != "adult_group"`.

        A new band that answered that question wrongly would silently lose the
        sibling and cheque-idioma discounts for the families on it. Priced
        through `_standard_period_price`, i.e. through the generator's own
        arithmetic.
        """
        from billing.services.pricing_service import PricingService

        amount = PricingService._standard_period_price(
            site_config, months=[10], schedule_type="part_time_child", cheque=True
        )
        assert amount == site_config.part_time_child_monthly_fee - site_config.language_cheque_discount

    def test_the_sibling_discount_comes_off_it(self, site_config):
        from billing.money import round_money
        from billing.services.pricing_service import PricingService

        amount = PricingService._standard_period_price(
            site_config, months=[10], schedule_type="part_time_child", sibling=True
        )
        expected = round_money(
            site_config.part_time_child_monthly_fee * (1 - site_config.sibling_discount / Decimal("100"))
        )
        assert amount == expected

    def test_june_discounts_it_like_any_other_child_band(self, site_config):
        from billing.services.pricing_service import PricingService

        amount = PricingService._standard_period_price(site_config, months=[6], schedule_type="part_time_child")
        assert amount == site_config.part_time_child_monthly_fee - site_config.june_discount


class TestItIsEditableFromManagement:
    def test_the_price_endpoint_accepts_it(self, authenticated_client, site_config):
        import json

        from django.urls import reverse

        from billing.models import SiteConfiguration

        response = authenticated_client.post(
            reverse("update_site_config"),
            data=json.dumps({"part_time_child_monthly_fee": "30.50"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert SiteConfiguration.get_config(refresh=True).part_time_child_monthly_fee == Decimal("30.50")

    def test_the_management_page_renders_the_input(self, authenticated_client, site_config):
        from django.urls import reverse

        page = authenticated_client.get(reverse("management")).content.decode()
        assert 'name="part_time_child_monthly_fee"' in page
