"""Pure money math for billing — the ONE rounding and the shared price formulas.

This module is a **leaf**: it imports only the stdlib. Nothing here imports
`billing.models` or `billing.services.*`, which is the whole point — both
`billing.models` (`Enrollment.save()`'s price fallback) and
`billing.services.pricing_service` need these helpers, and having `models`
import them from `pricing_service` created a `models → pricing_service → models`
import cycle (pricing_service reaches back into `billing.models` for
`SiteConfiguration.get_config()`). CodeQL flagged it; the structural fix is to
put the model-free math in a module that sits below both.

`pricing_service` re-exports every name here, so existing
`from billing.services.pricing_service import round_money` imports keep working.
"""

from decimal import ROUND_HALF_UP, Decimal

#: The smallest amount any money field will hold. `Payment.amount` and
#: `Enrollment.final_amount` both validate `MinValueValidator(0.01)`, and
#: `objects.create()` does not run validators — so an unfloored 0.00 would
#: persist and then sit on the ficha as an uncollectable debt.
MONEY_QUANTUM = Decimal("0.01")

#: Schedule type → the `SiteConfiguration` monthly-fee attribute it bills at.
_SCHEDULE_FEE_ATTR = {
    "full_time": "full_time_monthly_fee",
    "part_time": "part_time_monthly_fee",
    "part_time_child": "part_time_child_monthly_fee",
    "adult_group": "adult_group_monthly_fee",
}


def round_money(value):
    """Floor at €0.01 and quantize HALF_UP — the ONE money rounding in billing.

    Every consumer must see the same cents. Unquantized, the `DecimalField` save
    path rounds HALF_EVEN, the payment generator rounded HALF_UP and a dry run's
    ``f"{amount:.2f}"`` rounded HALF_EVEN again — so a half-cent intermediate
    (quarterly + sibling on the default prices lands exactly on 146.205) printed
    146.20 in the preview and billed 146.21 on the invoice.
    """
    return max(Decimal(value), MONEY_QUANTUM).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def monthly_fee_for(schedule_type, config):
    """The standard monthly fee for a schedule type, read off `config`.

    Unknown schedule types fall back to full-time, matching
    `PricingService.get_monthly_fee` (which delegates here).
    """
    return getattr(config, _SCHEDULE_FEE_ATTR.get(schedule_type, "full_time_monthly_fee"))


def quarterly_price_from_monthly(monthly_fee, config):
    """A full quarter's price: three months MINUS the configured quarterly discount.

    Parameterised by the monthly fee rather than reading
    `config.full_time_monthly_fee` itself, because the three callers start from
    different bases: the advertised quarterly price is always full-time, while
    `Enrollment.save()`'s fallback and `EnrollmentService` must apply the same
    formula to whatever base `schedule_type` selected.

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
    `Enrollment.save()`'s fallback and `EnrollmentService` so a plan re-issued by
    the app and one created by hand in the admin cannot be priced differently.
    """
    monthly = monthly_fee_for(schedule_type, config)
    if payment_modality == "quarterly":
        return quarterly_price_from_monthly(monthly, config)
    return monthly
