from contextvars import ContextVar
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.core.signals import request_finished, request_started
from django.core.validators import MinValueValidator
from django.db import models

from billing import constants

# Enrolment for the next course opens in May, so from May onwards "the current
# academic year" is the one families are signing up for — not the one whose
# classes are still finishing. Before this, a student enrolled in, say, August
# 2026 was given the 2025-2026 year, whose teaching period had already ended:
# their enrollment_period_start landed in September 2025 and the whole schedule
# of monthly payments was generated in the past.
ACADEMIC_YEAR_ROLLOVER_MONTH = 5  # May

# Classes themselves run September→June. That is a different question from the
# one above and must not be conflated — see academic_year_for_month().
TEACHING_YEAR_START_MONTH = 9  # September


# Request-scoped cache for the SiteConfiguration singleton.
#
# `get_config()` is called from 26 places and every call was its own query — the
# pricing services, the payment generators, the admin and several views all ask
# for it, often more than once in a single request. A ContextVar rather than
# Django's cache framework is deliberate: this holds LIVE PRICES, and a stale
# entry would mis-bill a family. Within one request the config cannot change, so
# the memo is always correct; `save()` clears it immediately, which also covers
# the long-lived Celery worker, and `request_started` clears it between requests
# in case a thread is reused.
_CONFIG_CACHE: ContextVar = ContextVar("siteconfiguration_cache", default=None)


def _clear_config_cache(**_kwargs):
    _CONFIG_CACHE.set(None)


request_started.connect(_clear_config_cache, dispatch_uid="billing.clear_siteconfig_cache.start")
request_finished.connect(_clear_config_cache, dispatch_uid="billing.clear_siteconfig_cache.end")


def _academic_year_from(reference_date, rollover_month):
    start_year = reference_date.year if reference_date.month >= rollover_month else reference_date.year - 1
    return f"{start_year}-{start_year + 1}"


def current_academic_year(reference_date=None):
    """
    Return the academic year being enrolled into, in YYYY-YYYY format.

    Rolls over in **May**, when enrolment for the next course opens — not in
    September when classes start. So in August 2026 this is "2026-2027": the
    course a family signing up today is joining.

    For attributing an existing teaching month to a course, use
    `academic_year_for_month()` instead.
    """
    reference_date = reference_date or date.today()
    return _academic_year_from(reference_date, ACADEMIC_YEAR_ROLLOVER_MONTH)


def academic_year_for_month(reference_date=None):
    """
    Return the academic year a given **teaching month** belongs to.

    Classes run September→June, so May 2026 belongs to 2025-2026 even though
    enrolment for 2026-2027 is already open by then. Payment generation must use
    this: billing a May fee against the year families are only just signing up
    for would match no active enrollment and silently generate nothing.
    """
    reference_date = reference_date or date.today()
    return _academic_year_from(reference_date, TEACHING_YEAR_START_MONTH)


def relevant_academic_years(reference_date=None):
    """
    Return every academic year that counts as "current" for display and queries.

    Between May and August two cohorts coexist: the students finishing the
    running course, and those already signed up for the next one. A view that
    filters on a single year hides one of them — filtering on the enrolment year
    alone would empty the student list every 1 May, mid-course, while filtering
    on the teaching year alone would make a student enrolled in August invisible
    until September.

    Outside that window both helpers agree and this is a single value, so the
    behaviour is unchanged for eight months of the year.
    """
    reference_date = reference_date or date.today()
    years = {
        academic_year_for_month(reference_date),
        current_academic_year(reference_date),
    }
    return sorted(years)


def academic_year_start_date(year):
    """
    Return the first Monday on or after September 14th of the given year.
    This is the start of the academic year (3rd week of September).
    """
    sept_14 = date(year, 9, 14)
    # Monday = 0, so we need to find the next Monday on or after Sept 14
    days_until_monday = (7 - sept_14.weekday()) % 7
    return sept_14 + timedelta(days=days_until_monday)


def academic_year_end_date(year):
    """
    Return the last Friday of June of the given year.
    """
    # Start from June 30 and go backwards to find Friday (weekday=4)
    june_30 = date(year, 6, 30)
    days_since_friday = (june_30.weekday() - 4) % 7
    return june_30 - timedelta(days=days_since_friday)


# ============================================================================
# SITE CONFIGURATION - Singleton para configuración del sitio
# ============================================================================


class _SingletonQuerySet(models.QuerySet):
    """Blocks queryset-level deletes of a singleton row.

    Overriding only `Model.delete()` leaves `Model.objects.all().delete()` wide
    open — that path never touches the instance method and silently wiped the
    configuration row (taking every price with it).
    """

    def delete(self):
        return (0, {})


class SiteConfiguration(models.Model):
    """
    Modelo singleton para almacenar configuración editable del sitio.
    Solo debe existir una instancia de este modelo.
    """

    objects = _SingletonQuerySet.as_manager()

    # Matrícula (Enrollment Fees)
    children_enrollment_fee = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("40.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name="Matrícula niños",
    )
    adult_enrollment_fee = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("20.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name="Matrícula adultos",
    )

    # Mensualidades (Monthly Fees)
    full_time_monthly_fee = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("54.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name="Mensualidad jornada completa",
    )
    part_time_monthly_fee = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("36.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name="Mensualidad media jornada",
    )
    adult_group_monthly_fee = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("60.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name="Mensualidad grupo adultos",
    )

    # Descuentos (Discounts)
    language_cheque_discount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("20.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name="Cheque idioma (€ fijo)",
    )
    quarterly_enrollment_discount = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("5.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name="Matrícula trimestral (%)",
    )
    old_student_discount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("20.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name="Alumno antiguo (€ fijo)",
    )
    june_discount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("20.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name="Descuento junio — completar año (€ fijo)",
    )
    full_year_bonus = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("20.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name="Año completo (€ fijo, no adultos)",
    )
    sibling_discount = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("5.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name="Hermanos (% mensual)",
    )
    half_month_discount = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("50.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name="Medio mes — septiembre (%)",
    )
    one_week_discount = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("75.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name="Solo 1 semana — primer mes (%)",
    )
    three_week_discount = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("25.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name="Solo 3 semanas (%)",
    )
    # v1.13 — returning-student enrollment discount. Flat euros knocked off
    # the one-time enrollment fee when a student re-enrols in a later
    # academic year. Auto-detected by EnrollmentService (any prior
    # Enrollment for the same student, any status, any earlier academic
    # year). Stacks with sibling + language-cheque discounts.
    returning_student_enrollment_discount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("20.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name="Estudiante recurrente (€ fijo sobre la matrícula)",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "site_configuration"
        verbose_name = "Configuración del sitio"
        verbose_name_plural = "Configuración del sitio"

    def __str__(self):
        return "Configuración del sitio"

    def save(self, *args, **kwargs):
        """Ensure only one instance exists (singleton pattern)"""
        self.pk = 1
        super().save(*args, **kwargs)
        # Any write invalidates the request-scoped memo immediately, so an
        # admin editing prices in /management/ cannot be served the old ones.
        _CONFIG_CACHE.set(None)

    def delete(self, *args, **kwargs):
        """Prevent deletion of the singleton.

        Returns the (count, per-model-counts) tuple Django's delete() contract
        promises — returning None broke callers that unpack it (the admin's
        bulk-delete action among them).
        """
        return (0, {})

    @classmethod
    def get_config(cls, refresh: bool = False):
        """
        Obtiene la configuración del sitio (crea una si no existe).
        Usa valores por defecto de constants.py si no hay configuración.

        Memoised for the duration of the request — see `_CONFIG_CACHE`. Pass
        `refresh=True` to force a re-read (nothing needs to today; it exists so
        a caller that has just written through a path bypassing `save()` is not
        stuck with a stale object).
        """
        if not refresh:
            cached = _CONFIG_CACHE.get()
            if cached is not None:
                return cached

        config, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                "children_enrollment_fee": constants.CHILDREN_ENROLLMENT_FEE,
                "adult_enrollment_fee": constants.ADULT_ENROLLMENT_FEE,
                "full_time_monthly_fee": constants.FULL_TIME_MONTHLY_FEE,
                "part_time_monthly_fee": constants.PART_TIME_MONTHLY_FEE,
                "adult_group_monthly_fee": constants.ADULT_GROUP_MONTHLY_FEE,
                "language_cheque_discount": constants.LANGUAGE_CHEQUE_DISCOUNT[0],
                "quarterly_enrollment_discount": constants.QUARTERLY_ENROLLMENT_DISCOUNT[0],
                "old_student_discount": constants.OLD_STUDENT_DISCOUNT[0],
                "june_discount": constants.JUNE_DISCOUNT[0],
                "full_year_bonus": constants.FULL_YEAR_BONUS[0],
                "sibling_discount": constants.SIBLING_DISCOUNT[0],
                "half_month_discount": constants.HALF_MONTH_DISCOUNT[0],
                "one_week_discount": constants.ONE_WEEK_DISCOUNT[0],
                "three_week_discount": constants.THREE_WEEK_DISCOUNT[0],
                "returning_student_enrollment_discount": constants.RETURNING_STUDENT_ENROLLMENT_DISCOUNT,
            },
        )
        _CONFIG_CACHE.set(config)
        return config


class EnrollmentType(models.Model):
    name = models.CharField(max_length=20, choices=constants.ENROLLMENT_TYPE_CHOICES, unique=True)
    display_name = models.CharField(max_length=50)
    base_amount_full_time = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    base_amount_part_time = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "enrollment_types"
        verbose_name = "Tipo de matrícula"
        verbose_name_plural = "Tipos de matrícula"

    def __str__(self):
        return self.display_name


class Enrollment(models.Model):
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="enrollments")
    enrollment_type = models.ForeignKey(EnrollmentType, on_delete=models.PROTECT, related_name="enrollments")

    enrollment_period_start = models.DateField()
    enrollment_period_end = models.DateField()
    academic_year = models.CharField(max_length=9, default=current_academic_year)
    schedule_type = models.CharField(max_length=20, choices=constants.SCHEDULE_TYPE_CHOICES, default="full_time")
    payment_modality = models.CharField(
        max_length=10, choices=constants.PAYMENT_MODALITY_CHOICES, default="monthly", verbose_name="Modalidad de pago"
    )
    has_language_cheque = models.BooleanField(default=False, verbose_name="Cheque idioma")
    is_sibling_discount = models.BooleanField(default=False, verbose_name="Descuento hermano")

    enrollment_amount = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(constants.MIN_ENROLLMENT_AMOUNT)]
    )
    discount_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(Decimal("0.00"))]
    )
    final_amount = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(constants.MIN_ENROLLMENT_AMOUNT)]
    )

    status = models.CharField(max_length=10, choices=constants.ENROLLMENT_STATUS_CHOICES, default="pending")
    enrollment_date = models.DateField()

    document_url = models.URLField(blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "enrollments"
        verbose_name = "Matrícula"
        verbose_name_plural = "Matrículas"
        indexes = [
            models.Index(fields=["student"]),
            models.Index(fields=["status"]),
            models.Index(fields=["academic_year"]),
            models.Index(fields=["enrollment_date"]),
            models.Index(fields=["enrollment_period_start"]),
        ]
        # Prevent overlapping active enrollments for the same student
        constraints = [
            models.UniqueConstraint(
                fields=["student"], condition=models.Q(status="active"), name="unique_active_enrollment_per_student"
            )
        ]

    def __str__(self):
        return f"{self.student} - {self.enrollment_type} ({self.get_schedule_type_display()})"

    def save(self, *args, **kwargs):
        """
        Auto-calculate final_amount from the configured fees for this schedule/modality.

        `final_amount` is the recurring period fee, so it is read from SiteConfiguration
        and NOT from `enrollment_type.base_amount_*` — an EnrollmentType is a matrícula
        category and its amounts are the one-time matrícula fee. EnrollmentService always
        supplies `final_amount`; this fallback only covers enrollments created by hand
        (admin, shell, data migrations).
        """
        if not self.final_amount:
            config = SiteConfiguration.get_config()
            if self.schedule_type == "adult_group":
                base_amount = config.adult_group_monthly_fee
            elif self.schedule_type == "part_time":
                base_amount = config.part_time_monthly_fee
            else:
                base_amount = config.full_time_monthly_fee
            if self.payment_modality == "quarterly":
                base_amount = base_amount * 3
            discount_amount = base_amount * (self.discount_percentage / Decimal("100"))
            self.final_amount = base_amount - discount_amount

        # Deliberately OUTSIDE the block above. It used to be nested inside it,
        # so passing final_amount but not enrollment_amount skipped the
        # fallback entirely and the insert died on a NOT NULL violation.
        if not self.enrollment_amount:
            self.enrollment_amount = self.final_amount

        super().save(*args, **kwargs)

    def payment_totals(self) -> "EnrollmentPaymentTotals":
        """Overdue money, outstanding money and how many payments exist. One query.

        This replaced a comparison that could not mean anything: `is_paid` summed
        EVERY completed payment on the enrollment — the matrícula and each
        month's cuota together — and compared the total to `final_amount`, which
        is the price of ONE period. The numerator and denominator were different
        units, so a monthly student owing 520 EUR across ten periods reported
        `is_paid=True` and `remaining_amount=0.00` the moment a single 54 EUR
        month was collected, with nine months still pending. A 40 EUR matrícula
        on its own left "remaining 14.00" — the enrollment fee counted against a
        monthly fee.

        The question the admin actually asks this row is "does this family owe me
        money?", so that is what is computed. `overdue` drives it: money that has
        passed its due date and is still pending, i.e. the chase list. Merely
        `outstanding` money is reported separately rather than folded in —
        payments are created on the first day of their period and only fall due
        on its last, so counting them as owed would flag nearly every family for
        most of every month and make the column unreadable.

        Only `pending` counts, so cancelled / failed / refunded money is
        excluded exactly as `billing.constants.LIVE_PAYMENT_STATUSES` intends.
        """
        today = date.today()
        overdue = Decimal("0.00")
        outstanding = Decimal("0.00")
        billed = 0
        for amount, due_date, status in self.payments.values_list("amount", "due_date", "payment_status"):
            billed += 1
            if status != "pending":
                continue
            outstanding += amount
            if due_date is not None and due_date < today:
                overdue += amount
        return EnrollmentPaymentTotals(overdue=overdue, outstanding=outstanding, billed=billed)

    @property
    def overdue_amount(self):
        """Invoiced, past its due date, still unpaid."""
        return self.payment_totals().overdue

    @property
    def outstanding_amount(self):
        """Invoiced and not yet collected, whether or not it has fallen due."""
        return self.payment_totals().outstanding

    @property
    def is_up_to_date(self):
        """True when nothing on this enrollment is past due.

        Deliberately not called `is_paid`: an active enrollment is never "paid"
        in full until its last period is collected, and a name that claims
        otherwise is what let the old figure go unquestioned.
        """
        return self.payment_totals().overdue == Decimal("0.00")


class EnrollmentPaymentTotals(NamedTuple):
    """What one enrollment owes, resolved in a single query."""

    overdue: Decimal
    outstanding: Decimal
    # NOT `count`: a NamedTuple field of that name shadows
    # `tuple.count`, which mypy rejects outright.
    billed: int


class Payment(models.Model):
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="payments")
    enrollment = models.ForeignKey(Enrollment, on_delete=models.PROTECT, related_name="payments", null=True, blank=True)
    parent = models.ForeignKey(
        "students.Parent", on_delete=models.PROTECT, related_name="payments", null=True, blank=True
    )

    payment_type = models.CharField(max_length=20, choices=constants.PAYMENT_TYPE_CHOICES, default="monthly")
    payment_method = models.CharField(max_length=15, choices=constants.PAYMENT_METHOD_CHOICES, default="transfer")

    amount = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(constants.MIN_PAYMENT_AMOUNT)]
    )
    currency = models.CharField(max_length=3, default=constants.DEFAULT_CURRENCY)

    payment_status = models.CharField(max_length=10, choices=constants.PAYMENT_STATUS_CHOICES, default="pending")
    due_date = models.DateField()
    payment_date = models.DateField(null=True, blank=True)

    concept = models.CharField(max_length=200)
    reference_number = models.CharField(max_length=50, blank=True)  # Bank reference, receipt number, etc.

    # v1.11 — Stripe reconciliation
    stripe_session_id = models.CharField(
        max_length=200,
        blank=True,
        db_index=True,
        help_text="Stripe Checkout session id; populated when a payment link is issued.",
    )
    stripe_payment_intent = models.CharField(
        max_length=200,
        blank=True,
        db_index=True,
        help_text="Stripe PaymentIntent id; populated when the webhook fires on session completion.",
    )

    observations = models.TextField(blank=True)
    document_url = models.URLField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments"
        verbose_name = "Pago"
        verbose_name_plural = "Pagos"
        indexes = [
            models.Index(fields=["student"]),
            models.Index(fields=["parent"]),
            models.Index(fields=["payment_status"]),
            models.Index(fields=["due_date"]),
            models.Index(fields=["payment_date"]),
            models.Index(fields=["enrollment"]),
        ]

    def __str__(self):
        return f"{self.student} - {self.concept} - €{self.amount} ({self.get_payment_status_display()})"

    def clean(self):
        """Validation logic"""

        # If payment is completed, payment_date should be set
        if self.payment_status == "completed" and not self.payment_date:
            self.payment_date = date.today()

        # Payment date should not be in the future for completed payments
        if self.payment_status == "completed" and self.payment_date and self.payment_date > date.today():
            raise ValidationError("Payment date cannot be in the future for completed payments.")

        # Validate student-parent relationship (skip for adult students)
        if self.student and self.parent and not self.student.is_adult:
            if not self.student.parents.filter(id=self.parent.id).exists():
                raise ValidationError("The selected parent is not associated with this student.")

    @property
    def is_overdue(self):
        """Check if payment is overdue"""
        return self.payment_status == "pending" and self.due_date is not None and self.due_date < date.today()

    @property
    def days_overdue(self):
        """Calculate days overdue"""
        if self.is_overdue:
            return (date.today() - self.due_date).days
        return 0


# ============================================================================
# EXPENSE TRACKING (v1.5)
# ============================================================================


class Expense(models.Model):
    """
    A single academy expense (rent, supplies, salaries, utilities, other).

    Recurring expenses are represented by `is_recurring=True` on a template row.
    The cadence is controlled by `recurring_frequency`:

    - ``monthly`` — every calendar month on ``recurring_day`` (1-28).
    - ``yearly``  — once a year on ``recurring_day`` + ``recurring_month``.
    - ``weekly``  — on each weekday listed in ``recurring_weekdays`` (ints 0-6,
      Monday=0 … Sunday=6, matching ``date.weekday()``).

    Monthly templates are materialised by a monthly Celery Beat job; weekly and
    yearly templates by a daily job. Materialisation creates concrete Expense
    rows so historical reporting stays honest — the template itself is never
    counted twice.
    """

    EXPENSE_CATEGORY_CHOICES = [
        ("rent", "Alquiler"),
        ("salaries", "Salarios"),
        ("supplies", "Material"),
        ("utilities", "Suministros"),
        ("marketing", "Marketing"),
        ("software", "Software / Suscripciones"),
        ("insurance", "Seguros"),
        ("taxes", "Impuestos"),
        ("other", "Otros"),
    ]

    RECURRING_FREQUENCY_CHOICES = [
        ("monthly", "Mensual"),
        ("yearly", "Anual"),
        ("weekly", "Semanal"),
    ]

    # date.weekday(): Monday=0 … Sunday=6. Labels are Spanish (UI).
    WEEKDAY_CHOICES = [
        (0, "Lunes"),
        (1, "Martes"),
        (2, "Miércoles"),
        (3, "Jueves"),
        (4, "Viernes"),
        (5, "Sábado"),
        (6, "Domingo"),
    ]

    description = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=EXPENSE_CATEGORY_CHOICES, default="other")
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    expense_date = models.DateField(default=date.today)
    notes = models.TextField(blank=True)

    is_recurring = models.BooleanField(default=False)
    recurring_frequency = models.CharField(
        max_length=10,
        choices=RECURRING_FREQUENCY_CHOICES,
        default="monthly",
        help_text="Cadence of a recurring template: monthly, yearly or weekly.",
    )
    recurring_day = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Day of the month (1–31). Used by monthly + yearly templates. "
            "Days past the end of a short month fall back to that month's last "
            "day, so 31 means 'last day of the month'."
        ),
    )
    recurring_month = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Month (1–12) when a yearly template should materialise.",
    )
    recurring_weekdays = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Comma-separated weekday ints (0=Mon … 6=Sun) for weekly templates.",
    )
    # Link back to the template row when the record was auto-generated.
    generated_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_children",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "expenses"
        verbose_name = "Gasto"
        verbose_name_plural = "Gastos"
        ordering = ["-expense_date", "-created_at"]
        indexes = [
            models.Index(fields=["expense_date"]),
            models.Index(fields=["category"]),
            models.Index(fields=["is_recurring"]),
        ]

    def __str__(self):
        return f"{self.description} — {self.amount}€ ({self.get_category_display()})"

    def weekday_set(self):
        """Return the set of weekday ints (0-6) for a weekly template."""
        if not self.recurring_weekdays:
            return set()
        return {int(x) for x in self.recurring_weekdays.split(",") if x.strip() != ""}

    def recurring_summary(self):
        """Human-readable (Spanish) description of a recurring template's cadence."""
        if not self.is_recurring:
            return ""
        if self.recurring_frequency == "monthly":
            if self.recurring_day and self.recurring_day >= 29:
                return "Mensual · último día del mes"
            return f"Mensual · día {self.recurring_day}"
        if self.recurring_frequency == "yearly":
            months = dict(
                [
                    (1, "enero"),
                    (2, "febrero"),
                    (3, "marzo"),
                    (4, "abril"),
                    (5, "mayo"),
                    (6, "junio"),
                    (7, "julio"),
                    (8, "agosto"),
                    (9, "septiembre"),
                    (10, "octubre"),
                    (11, "noviembre"),
                    (12, "diciembre"),
                ]
            )
            return f"Anual · {self.recurring_day} de {months.get(self.recurring_month, '?')}"
        if self.recurring_frequency == "weekly":
            labels = dict(self.WEEKDAY_CHOICES)
            days = ", ".join(labels[d] for d in sorted(self.weekday_set()))
            return f"Semanal · {days}"
        return ""

    def clean(self):
        if not self.is_recurring:
            return

        if self.recurring_frequency == "monthly":
            if not self.recurring_day:
                raise ValidationError("Monthly recurring expenses must set recurring_day (1–31).")
            if not (1 <= self.recurring_day <= 31):
                raise ValidationError("recurring_day must be between 1 and 31.")
        elif self.recurring_frequency == "yearly":
            if not self.recurring_day:
                raise ValidationError("Yearly recurring expenses must set recurring_day (1–31).")
            if not (1 <= self.recurring_day <= 31):
                raise ValidationError("recurring_day must be between 1 and 31.")
            if not self.recurring_month:
                raise ValidationError("Yearly recurring expenses must set recurring_month (1–12).")
            if not (1 <= self.recurring_month <= 12):
                raise ValidationError("recurring_month must be between 1 and 12.")
        elif self.recurring_frequency == "weekly":
            weekdays = self.weekday_set()
            if not weekdays:
                raise ValidationError("Weekly recurring expenses must select at least one weekday.")
            if any(not (0 <= d <= 6) for d in weekdays):
                raise ValidationError("recurring_weekdays must contain ints between 0 (Mon) and 6 (Sun).")
        else:
            raise ValidationError("recurring_frequency must be monthly, yearly or weekly.")
