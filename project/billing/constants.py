from decimal import Decimal

# ============================================================================
# MATRÍCULA (ENROLLMENT FEES)
# ============================================================================
# Precios base de matrícula anual

CHILDREN_ENROLLMENT_FEE = Decimal("40.00")  # Matrícula niños (1 año)
ADULT_ENROLLMENT_FEE = Decimal("20.00")  # Matrícula adultos (1 año)


# ============================================================================
# MENSUALIDADES (MONTHLY FEES)
# ============================================================================
# Precios base mensuales según tipo de horario

FULL_TIME_MONTHLY_FEE = Decimal("54.00")  # Jornada completa (2 clases/semana)
PART_TIME_MONTHLY_FEE = Decimal("36.00")  # Media jornada (1 clase/semana)
ADULT_GROUP_MONTHLY_FEE = Decimal("60.00")  # Grupo adultos (1 clase/semana)


# ============================================================================
# DESCUENTOS (DISCOUNTS)
# ============================================================================
# Formato: (valor, tipo) donde tipo puede ser 'flat' (cantidad fija) o 'percentage' (porcentaje)

LANGUAGE_CHEQUE_DISCOUNT = (Decimal("20.00"), "flat")  # Cheque idioma
QUARTERLY_ENROLLMENT_DISCOUNT = (Decimal("5.00"), "percentage")  # Matrícula trimestral (5%)
OLD_STUDENT_DISCOUNT = (Decimal("20.00"), "flat")  # Alumno antiguo (-20€)
JUNE_DISCOUNT = (Decimal("20.00"), "flat")  # Descuento junio (completar año, NO adultos)
FULL_YEAR_BONUS = (Decimal("20.00"), "flat")  # Año completo (NO adultos)
SIBLING_DISCOUNT = (Decimal("5.00"), "percentage")  # Hermanos (5% cada mes)
HALF_MONTH_DISCOUNT = (Decimal("50.00"), "percentage")  # Medio mes (septiembre)
ONE_WEEK_DISCOUNT = (Decimal("75.00"), "percentage")  # Solo 1 semana (primer mes)
THREE_WEEK_DISCOUNT = (Decimal("25.00"), "percentage")  # Solo 3 semanas
# v1.13 — returning-student enrollment discount (flat euros off the one-time
# matrícula charge). Applied automatically by EnrollmentService when the
# student has at least one prior Enrollment for a different academic year.
# Stacks with sibling + language-cheque.
RETURNING_STUDENT_ENROLLMENT_DISCOUNT = Decimal("20.00")


# ============================================================================
# MONEDA (CURRENCY)
# ============================================================================

DEFAULT_CURRENCY = "EUR"


# ============================================================================
# CHOICES - Opciones para modelos
# ============================================================================

# An EnrollmentType is a MATRÍCULA category — who is being enrolled — not a payment
# cadence. Monthly vs quarterly is `Enrollment.payment_modality`, and full/part time is
# `Enrollment.schedule_type`; modelling them here too meant "Mensual" / "Trimestral"
# appeared as the enrolment type in the matriculation email while saying nothing about
# the matrícula actually charged. There are exactly four.
ENROLLMENT_TYPE_CHOICES = [
    ("new_student", "New Student"),
    ("returning_student", "Returning Student"),
    ("adults", "Adults"),
    ("special", "Special"),
]

# Spanish labels for EnrollmentType.display_name. The keys above are internal
# identifiers (English, like every other choice key in the project); this map is
# what a PARENT sees — it lands in the matriculation and welcome emails via
# `enrollment.enrollment_type.display_name`. Seeding those rows with the English
# key meant the Spanish email said "Monthly" / "Quarterly".
ENROLLMENT_TYPE_DISPLAY_ES = {
    "new_student": "Nuevo estudiante",
    "returning_student": "Antiguo estudiante",
    "adults": "Adulto",
    "special": "Especial",
}

SCHEDULE_TYPE_CHOICES = [
    ("full_time", "2 días/semana"),
    ("part_time", "1 día/semana"),
    ("adult_group", "Adultos (1 día/semana)"),
]

PAYMENT_MODALITY_CHOICES = [
    ("monthly", "Mensual"),
    ("quarterly", "Trimestral"),
]


# Every label below is rendered directly to the user through
# `get_<field>_display()` — the payment detail page, the payments list, the
# student detail page and the admin all read them. They must be Spanish: an
# English "Monthly Fee" in the middle of a Spanish page is what prompted this.
ENROLLMENT_STATUS_CHOICES = [
    ("pending", "Pendiente"),
    ("active", "Activa"),
    ("finished", "Finalizada"),
    ("cancelled", "Cancelada"),
    ("suspended", "Suspendida"),
]

PAYMENT_METHOD_CHOICES = [
    ("cash", "Efectivo"),
    ("transfer", "Transferencia"),
    ("credit_card", "Tarjeta"),
]

PAYMENT_STATUS_CHOICES = [
    ("pending", "Pendiente"),
    ("completed", "Completado"),
    ("failed", "Fallido"),
    ("cancelled", "Cancelado"),
    ("refunded", "Reembolsado"),
]

PAYMENT_TYPE_CHOICES = [
    ("enrollment", "Matrícula"),
    ("monthly", "Mensualidad"),
    ("quarterly", "Trimestre"),
    ("other", "Otro"),
]

# Statuses that represent money the academy still expects to collect (or has
# already collected). Everything else — cancelled, failed, refunded — is money
# that will never arrive and must NOT inflate "esperado" / expected revenue.
#
# Before this existed each view rolled its own filter: the payments list and the
# dashboard summed EVERY status into "esperado", so cancelling a duplicate
# payment left it counted as expected and dragged the collection rate down.
LIVE_PAYMENT_STATUSES = ("pending", "completed")


# The payment types the recurring schedule issues, as opposed to the one-off
# `enrollment` matrícula and ad-hoc `other` rows. Everything that reasons about
# "the billing schedule" filters on these: `PaymentService.billed_months_map`,
# `reconcile_payment_schedule`, and the
# `unique_pending_periodic_payment_per_month` constraint (which cannot import
# this — a constraint's deconstruction has to be a literal for migrations to
# compare, so `billing/models.py` repeats the tuple; keep the two in step).
PERIODIC_PAYMENT_TYPES = ("monthly", "quarterly")


# ============================================================================
# VALIDACIONES
# ============================================================================

MIN_PAYMENT_AMOUNT = Decimal("0.01")  # Mínimo importe para pagos
MIN_ENROLLMENT_AMOUNT = Decimal("0.01")  # Mínimo importe para matrículas


# ============================================================================
# UTILIDADES
# ============================================================================


def calculate_discount(base_amount: Decimal, discount: tuple) -> Decimal:
    """
    Calcula el descuento basado en el tipo

    Args:
        base_amount: Importe base
        discount: Tupla (valor, tipo) donde tipo es 'flat' o 'percentage'

    Returns:
        Importe del descuento

    Example:
        >>> calculate_discount(Decimal('100'), (Decimal('10'), 'flat'))
        Decimal('10.00')
        >>> calculate_discount(Decimal('100'), (Decimal('5'), 'percentage'))
        Decimal('5.00')
    """
    value, discount_type = discount

    if discount_type == "flat":
        return value
    elif discount_type == "percentage":
        return base_amount * (value / Decimal("100"))
    else:
        raise ValueError(f"Tipo de descuento no válido: {discount_type}")


def get_enrollment_fee(is_adult: bool = False) -> Decimal:
    """
    Obtiene el precio de matrícula según si es adulto o niño

    Args:
        is_adult: True si es adulto, False si es niño

    Returns:
        Importe de matrícula correspondiente
    """
    return ADULT_ENROLLMENT_FEE if is_adult else CHILDREN_ENROLLMENT_FEE
