from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace

from billing.constants import SEPTEMBER_CLASSES_START_DAY
from billing.models import SiteConfiguration

# The money math lives in `billing.money`, a leaf module that imports no models.
# `billing.models` imports these from there too — routing them through this
# service is what created the `models → pricing_service → models` cycle CodeQL
# flagged. Re-exported here so existing `from ...pricing_service import
# round_money` / `period_base_amount` / `quarterly_price_from_monthly` callers
# are unchanged.
from billing.money import (
    MONEY_QUANTUM,
    monthly_fee_for,
    period_base_amount,
    quarterly_price_from_monthly,
    round_money,
)
from billing.services.payment_service import PaymentService

__all__ = [
    "MONEY_QUANTUM",
    "PricingService",
    "REMINDER_SPECIAL_MONTHS",
    "monthly_fee_for",
    "period_base_amount",
    "quarterly_price_from_monthly",
    "round_money",
]


# The three months whose payment-reminder email is NOT the regular one, keyed
# on the calendar month number. September bills a partial month (classes start
# mid-month), June carries the "completed the course" discount on every monthly
# fee, and April is where a QUARTERLY family sees that same discount — the
# April-June block contains June. `payment_reminder_form` picks the template
# from this map; `test_all_emails` previews all three.
REMINDER_SPECIAL_MONTHS = {9: "september", 6: "june", 4: "april"}

# One template per case, all extending `emails/payment_reminder.html` so the
# IBAN / Bizum / opening-hours shell is written once and only the explanation
# and the tariff table change.
REMINDER_TEMPLATES = {
    None: "payment_reminder",
    "september": "payment_reminder_september",
    "june": "payment_reminder_june",
    "april": "payment_reminder_april",
}

# Appended to the email subject so the special month is visible in the inbox
# list, before the family opens it.
REMINDER_SUBJECT_SUFFIX = {
    None: "",
    "september": " (medio mes)",
    "june": " (último mes: descuento fin de curso)",
    "april": " (trimestre con descuento fin de curso)",
}


class PricingService:
    """Centralized pricing logic. SiteConfiguration is the single source of truth."""

    @staticmethod
    def get_config():
        return SiteConfiguration.get_config()

    @staticmethod
    def get_monthly_fee(schedule_type, config=None):
        """Get the monthly fee for a given schedule type.

        Delegates to `billing.money.monthly_fee_for` rather than carrying its own
        copy of the mapping — a new schedule type has to price the same here, in
        `Enrollment.save()`'s fallback and in the payment generator, or the ficha
        and the invoice disagree.
        """
        if config is None:
            config = PricingService.get_config()
        return monthly_fee_for(schedule_type, config)

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
    def _standard_period_price(
        config,
        *,
        months,
        schedule_type="full_time",
        quarterly=False,
        fraction=Decimal("1"),
        sibling=False,
        cheque=False,
    ):
        """Price a STANDARD period through THE billing arithmetic, with no enrollment.

        The reminder email quotes what a family will be billed, so the figure
        has to come out of `PaymentService.calculate_period_amount` — the same
        call the generator prices with — and not out of a re-derivation here.
        The generator only reads five things off an enrollment (`schedule_type`,
        `is_sibling_discount`, `has_language_cheque`, `is_hand_priced`,
        `final_amount`), so a bare carrier with those attributes is enough; a
        never-hand-priced one prices the standard rate.

        Imported lazily: `payment_service` imports `billing.models` at module
        level, and this module must stay importable from there.
        """

        carrier = SimpleNamespace(
            schedule_type=schedule_type,
            is_sibling_discount=sibling,
            has_language_cheque=cheque,
            is_hand_priced=False,
            final_amount=None,
        )
        return PaymentService.calculate_period_amount(carrier, config, months, fraction, quarterly=quarterly)

    @staticmethod
    def cheque_idioma_price(config=None):
        """The standard full-time monthly fee with the Cheque Idioma applied (a Decimal).

        Priced through `_standard_period_price` rather than as `fee - discount`
        here, so the figure the reminder email quotes is by construction what
        the generator bills a cheque-idioma family for a full month.
        """
        if config is None:
            config = PricingService.get_config()
        return PricingService._standard_period_price(config, months=[10], cheque=True)

    @staticmethod
    def reminder_case_for_month(month_number):
        """`"september"`, `"june"`, `"april"` or None — which reminder email a month gets."""
        return REMINDER_SPECIAL_MONTHS.get(month_number)

    @staticmethod
    def payment_reminder_special(config=None, month_number=None, *, september_start_day=None):
        """Everything the reminder email needs for `month_number`, special case or not.

        Returns the five tariff rows of `payment_reminder_fees` ALREADY ADJUSTED
        for the month, the matching Cheque Idioma figure, the template to render
        and the subject suffix — plus every standard figure under a `standard_`
        prefix so the special templates can print "(cuota habitual: 54 euros)"
        beside the reduced one. Keys:

            special_case            None | "september" | "june" | "april"
            template_name           the `emails/<name>.html` to render
            subject_suffix          appended to the reminder subject
            full_time_fee, part_time_fee, part_time_child_fee, adult_fee,
            quarterly_fee, sibling_full_time_fee
                                    this month's figures (display strings)
            reduced_price_cheque_idioma   this month's Cheque Idioma figure
            standard_<each of the above>  the regular figures
            september_start_day, proration_percent   (September only)
            june_discount           the flat discount (June and April)

        The three cases, and why the figures are what they are:

        * **September** — classes start on `september_start_day` (default
          `SEPTEMBER_CLASSES_START_DAY`, "empezamos el 15"), so every MONTHLY
          row is the standard fee scaled by `PaymentService.proration_fraction`
          for that day — the same fraction the generator bills a 15-September
          enrollment with, so the email and the family's first receipt agree.
          The wording says "medio mes" because that is how the academy has
          always described it; the amount is the billed one, because a family
          transferring the figure in this email must not come up short against
          the payment the app is waiting for. The quarterly row is the full
          quarter, unchanged.
        * **June** — the last month of the course: every monthly fee carries
          `june_discount` (`price_breakdown` takes it off any period containing
          June, so this is the generator's own figure). Adults are excluded
          there and here. The quarterly row is unchanged: a quarterly family
          saw this discount in April.
        * **April** — the quarterly row is the April-June block, which contains
          June and therefore carries the discount; monthly rows are unchanged.

        Every figure is the output of `_standard_period_price`, i.e. of the
        generator's arithmetic, never `fee - 20` typed here.
        """
        if config is None:
            config = PricingService.get_config()
        case = PricingService.reminder_case_for_month(month_number)
        standard = PricingService.payment_reminder_fees(config)
        standard_cheque = _euros(PricingService.cheque_idioma_price(config))

        context = {
            "special_case": case,
            "template_name": REMINDER_TEMPLATES[case],
            "subject_suffix": REMINDER_SUBJECT_SUFFIX[case],
            **standard,
            "reduced_price_cheque_idioma": standard_cheque,
            **{f"standard_{key}": value for key, value in standard.items()},
            "standard_reduced_price_cheque_idioma": standard_cheque,
            "june_discount": _euros(config.june_discount),
        }

        price = PricingService._standard_period_price
        if case == "september":
            day = PricingService._september_start_day(september_start_day)
            year = date.today().year  # September always has 30 days; the year is immaterial
            fraction = PaymentService.proration_fraction(date(year, 9, day), 9, year)
            context.update(
                {
                    "september_start_day": day,
                    "proration_percent": int((fraction * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
                    "full_time_fee": _euros(price(config, months=[9], fraction=fraction)),
                    "part_time_fee": _euros(price(config, months=[9], schedule_type="part_time", fraction=fraction)),
                    "part_time_child_fee": _euros(
                        price(config, months=[9], schedule_type="part_time_child", fraction=fraction)
                    ),
                    "adult_fee": _euros(price(config, months=[9], schedule_type="adult_group", fraction=fraction)),
                    "sibling_full_time_fee": _euros(price(config, months=[9], fraction=fraction, sibling=True)),
                    "reduced_price_cheque_idioma": _euros(price(config, months=[9], fraction=fraction, cheque=True)),
                }
            )
        elif case == "june":
            context.update(
                {
                    "full_time_fee": _euros(price(config, months=[6])),
                    "part_time_fee": _euros(price(config, months=[6], schedule_type="part_time")),
                    "part_time_child_fee": _euros(price(config, months=[6], schedule_type="part_time_child")),
                    "adult_fee": _euros(price(config, months=[6], schedule_type="adult_group")),
                    "sibling_full_time_fee": _euros(price(config, months=[6], sibling=True)),
                    "reduced_price_cheque_idioma": _euros(price(config, months=[6], cheque=True)),
                }
            )
        elif case == "april":
            context["quarterly_fee"] = _euros(price(config, months=[4, 5, 6], quarterly=True))

        return context

    @staticmethod
    def _september_start_day(raw):
        """`raw` as a day of September (1-30), or the configured default.

        The form posts it as text; anything that is not a day in that range —
        blank, "abc", 0, 31 — falls back rather than 500ing the preview.
        """
        try:
            day = int(str(raw).strip())
        except (TypeError, ValueError):
            return SEPTEMBER_CLASSES_START_DAY
        return day if 1 <= day <= 30 else SEPTEMBER_CLASSES_START_DAY

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
            # Media jornada infantil — the same one-session-a-week timetable at
            # the reduced band for the youngest children. It rides WITH the
            # part-time row rather than as a row of its own: it is the same
            # class, and a sixth line in a five-line table reads like a sixth
            # product. The templates print it as a sub-line under "Cuota 1
            # sesión semanal".
            "part_time_child_fee": _euros(config.part_time_child_monthly_fee),
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
