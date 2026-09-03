from decimal import ROUND_HALF_UP, Decimal

#: The smallest amount any money field will hold (`billing.constants.MIN_PAYMENT_AMOUNT`
#: / `MIN_ENROLLMENT_AMOUNT`, repeated here so this module stays importable from
#: `billing.models` without a cycle — `billing.constants` imports nothing, but
#: keeping the literal local documents WHY 0.01 is the floor at the one place that
#: applies it).
MONEY_QUANTUM = Decimal("0.01")


def round_money(value):
    """Floor at €0.01 and quantize HALF_UP — the ONE money rounding in billing.

    Every consumer must see the same cents. Unquantized, the `DecimalField` save
    path rounds HALF_EVEN, the payment generator rounded HALF_UP and a dry run's
    ``f"{amount:.2f}"`` rounded HALF_EVEN again — so a half-cent intermediate
    (quarterly + sibling on the default prices lands exactly on 146.205) printed
    146.20 in the preview and billed 146.21 on the invoice.

    It lives here rather than on `PaymentService` because three places need it and
    one of them is `billing.models` itself (`Enrollment.save()`'s price fallback),
    which cannot import the payment service — that module imports `billing.models`
    at load time. `PaymentService._round_money` and
    `EnrollmentService._apply_discounts` both delegate here; they used to carry a
    copy each, and the model a third.

    The €0.01 floor is not cosmetic: `Payment.amount` and
    `Enrollment.final_amount` both validate `MinValueValidator(0.01)`, and
    `objects.create()` does not run validators — so an unfloored 0.00 persisted
    happily and then sat on the ficha as an uncollectable debt.
    """
    return max(Decimal(value), MONEY_QUANTUM).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def quarterly_price_from_monthly(monthly_fee, config):
    """A full quarter's price: three months MINUS the configured quarterly discount.

    Parameterised by the monthly fee rather than reading
    `config.full_time_monthly_fee` itself, because the three callers start from
    different bases: the advertised quarterly price is always full-time, while
    `Enrollment.save()`'s fallback and `EnrollmentService` must apply the same
    formula to whatever base `schedule_type` selected. Hard-coding full-time in
    the shared helper is exactly what forced the three hand-rolled copies this
    replaces (and the one in `Enrollment.save()` had originally omitted the
    discount, so an admin-created quarterly enrollment showed 162.00 on the ficha
    while the generator billed 153.90).

    Deliberately NOT rounded: callers that persist the figure round it, and the
    advertised-price comparison in `tests/unit/test_pricing_matches_billing.py`
    pairs it with a rounded billing figure.
    """
    base = Decimal(monthly_fee) * 3
    return base - base * (Decimal(config.quarterly_enrollment_discount) / Decimal("100"))


def period_base_amount(config, schedule_type, payment_modality):
    """Standard price of ONE billing period for this schedule + cadence.

    "Period" is a month for a monthly plan and a quarter for a quarterly one, so
    this is the figure `Enrollment.final_amount` holds. Shared by
    `Enrollment.save()`'s fallback and `EnrollmentService.replicate_enrollment`
    so a plan re-issued by the app and one created by hand in the admin cannot be
    priced differently.
    """
    monthly = PricingService.get_monthly_fee(schedule_type, config)
    if payment_modality == "quarterly":
        return quarterly_price_from_monthly(monthly, config)
    return monthly


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
        """The advertised quarterly base price (3 months * full_time - discount%).

        A thin wrapper over `quarterly_price_from_monthly` so the advertised
        figure and the one `Enrollment.save()` / `EnrollmentService` derive from a
        part-time or adult base cannot drift apart.
        """
        if config is None:
            config = PricingService.get_config()
        return quarterly_price_from_monthly(config.full_time_monthly_fee, config)

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
