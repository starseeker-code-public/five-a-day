"""`PricingService` must advertise exactly what `PaymentService` bills.

The two compute the same prices from `SiteConfiguration` in two places, and
until now only a docstring said so. They cannot simply share a function: the
billing helpers price a period *for an Enrollment* (they read `schedule_type`,
`is_sibling_discount`, `has_language_cheque` and `enrollment_type` off the row),
while `PricingService` answers the standard-price question the payment-reminder
email and the pricing preview ask, with no enrollment in hand.

So the duplication stays and this file is what holds it together: change the
discount order or a percentage on either side and these fail. That matters
because the figures meet in front of a parent — the reminder email quotes a
price and the invoice charges one.
"""

from decimal import Decimal

import pytest

from billing.models import Enrollment
from billing.services.payment_service import PaymentService
from billing.services.pricing_service import PricingService, _euros

pytestmark = pytest.mark.django_db


def _enrollment(student, enrollment_type, *, schedule_type, modality, sibling=False, cheque=False):
    """A bare enrollment used only as a carrier of pricing flags.

    Unsaved on purpose — `calculate_period_amount` only reads attributes, and
    `Enrollment.save()` would fill `final_amount` from config and obscure what
    is being asserted.
    """
    return Enrollment(
        student=student,
        enrollment_type=enrollment_type,
        academic_year="2025-2026",
        schedule_type=schedule_type,
        payment_modality=modality,
        is_sibling_discount=sibling,
        has_language_cheque=cheque,
        enrollment_amount=Decimal("0.00"),
        discount_percentage=Decimal("0.00"),
        final_amount=Decimal("0.00"),
        status="active",
    )


class TestAdvertisedPricesMatchBilledPrices:
    @pytest.mark.parametrize(
        ("schedule_type", "month"),
        [("full_time", 10), ("part_time", 10), ("adult_group", 10)],
    )
    def test_plain_monthly_fee(self, student, enrollment_type_new_student, site_config, schedule_type, month):
        enrollment = _enrollment(student, enrollment_type_new_student, schedule_type=schedule_type, modality="monthly")

        billed = PaymentService.calculate_period_amount(enrollment, site_config, [month])
        advertised = PricingService.get_monthly_fee(schedule_type, site_config)

        assert advertised == billed

    def test_quarterly_price(self, student, enrollment_type_new_student, site_config):
        enrollment = _enrollment(student, enrollment_type_new_student, schedule_type="full_time", modality="quarterly")

        billed = PaymentService.calculate_period_amount(enrollment, site_config, [10, 11, 12], quarterly=True)
        advertised = PricingService.calculate_quarterly_price(site_config)

        assert advertised == billed

    def test_sibling_monthly_price(self, student, enrollment_type_new_student, site_config):
        enrollment = _enrollment(
            student, enrollment_type_new_student, schedule_type="full_time", modality="monthly", sibling=True
        )

        billed = PaymentService.calculate_period_amount(enrollment, site_config, [10])
        advertised = PricingService.calculate_sibling_price(site_config, "full_time")

        assert advertised == billed

    def test_a_changed_discount_moves_both_sides_together(self, student, enrollment_type_new_student, site_config):
        """The point of the pairing: config drives both, so neither can be
        pinned to a stale constant."""
        site_config.sibling_discount = Decimal("12.00")
        site_config.save()

        enrollment = _enrollment(
            student, enrollment_type_new_student, schedule_type="full_time", modality="monthly", sibling=True
        )

        billed = PaymentService.calculate_period_amount(enrollment, site_config, [10])
        advertised = PricingService.calculate_sibling_price(site_config, "full_time")

        assert advertised == billed
        assert advertised != site_config.full_time_monthly_fee


class TestReminderEmailTableMatchesBilling:
    """`payment_reminder_fees` is what the parent actually reads."""

    def test_every_row_matches_the_billed_figure(self, student, enrollment_type_new_student, site_config):
        rows = PricingService.payment_reminder_fees(site_config)

        expectations = {
            "full_time_fee": (
                _enrollment(student, enrollment_type_new_student, schedule_type="full_time", modality="monthly"),
                [10],
                False,
            ),
            "part_time_fee": (
                _enrollment(student, enrollment_type_new_student, schedule_type="part_time", modality="monthly"),
                [10],
                False,
            ),
            "adult_fee": (
                _enrollment(student, enrollment_type_new_student, schedule_type="adult_group", modality="monthly"),
                [10],
                False,
            ),
            "quarterly_fee": (
                _enrollment(student, enrollment_type_new_student, schedule_type="full_time", modality="quarterly"),
                [10, 11, 12],
                True,
            ),
            "sibling_full_time_fee": (
                _enrollment(
                    student, enrollment_type_new_student, schedule_type="full_time", modality="monthly", sibling=True
                ),
                [10],
                False,
            ),
        }

        for row, (enrollment, months, quarterly) in expectations.items():
            billed = PaymentService.calculate_period_amount(enrollment, site_config, months, quarterly=quarterly)
            assert rows[row] == _euros(billed), f"advertised {row} does not match what is billed"
