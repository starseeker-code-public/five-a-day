from decimal import ROUND_HALF_UP, Decimal


class PricingService:
    """Centralized pricing logic. SiteConfiguration is the single source of truth."""

    @staticmethod
    def get_config():
        from billing.models import SiteConfiguration

        return SiteConfiguration.get_config()

    @staticmethod
    def get_monthly_fee(schedule_type, config=None):
        """Get the monthly fee for a given schedule type."""
        if config is None:
            config = PricingService.get_config()
        fees = {
            "full_time": config.full_time_monthly_fee,
            "part_time": config.part_time_monthly_fee,
            "adult_group": config.adult_group_monthly_fee,
        }
        return fees.get(schedule_type, config.full_time_monthly_fee)

    @staticmethod
    def get_enrollment_fee(is_adult, config=None):
        """Get the enrollment fee based on student type."""
        if config is None:
            config = PricingService.get_config()
        return config.adult_enrollment_fee if is_adult else config.children_enrollment_fee

    @staticmethod
    def calculate_quarterly_price(config=None):
        """Calculate the quarterly base price (3 months * full_time - discount%)."""
        if config is None:
            config = PricingService.get_config()
        base = config.full_time_monthly_fee * 3
        discount = base * (config.quarterly_enrollment_discount / Decimal("100"))
        return base - discount

    @staticmethod
    def calculate_sibling_price(config=None, schedule_type="full_time"):
        """Monthly fee with the sibling discount applied.

        Same percentage and order of operations as
        ``PaymentService.calculate_period_amount``, so the figure advertised in
        the payment-reminder email matches what the sibling is actually billed.

        It is a deliberate re-derivation rather than a call: the billing helpers
        price a period *for an Enrollment*, and this answers the standard-price
        question with no enrollment in hand. The two are held together by
        ``tests/unit/test_pricing_matches_billing.py`` instead of by comment.
        """
        if config is None:
            config = PricingService.get_config()
        base = PricingService.get_monthly_fee(schedule_type, config)
        return base - base * (config.sibling_discount / Decimal("100"))

    @staticmethod
    def payment_reminder_fees(config=None):
        """Display-ready fee table for the `payment_reminder` email.

        The quarterly and sibling rows used to read "consultar en la academia";
        both are plain derivations of SiteConfiguration (the same ones
        PaymentService bills), so they are computed here and every caller that
        renders the template shares one source of truth.
        """
        if config is None:
            config = PricingService.get_config()
        return {
            "full_time_fee": _euros(config.full_time_monthly_fee),
            "part_time_fee": _euros(config.part_time_monthly_fee),
            "adult_fee": _euros(config.adult_group_monthly_fee),
            "quarterly_fee": _euros(PricingService.calculate_quarterly_price(config)),
            "sibling_full_time_fee": _euros(PricingService.calculate_sibling_price(config)),
        }


def _euros(amount) -> str:
    """Format a money amount the way the Spanish emails print it: comma
    decimal separator, and no ",00" tail on whole euros ("54", "51,30")."""
    value = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{value:.2f}".replace(".", ",")
    return text[:-3] if text.endswith(",00") else text
