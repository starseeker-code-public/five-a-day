"""
Core constants — shared locale data and scheduled app registry.
Pricing and billing constants live in billing/constants.py.
"""

# ============================================================================
# LOCALES - Nombres en español
# ============================================================================

DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES_ES = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]


# ============================================================================
# SCHEDULED APPS - Registry of scheduled apps/emails
# ============================================================================

SCHEDULED_APPS = [
    {"name": "Fun Friday", "url_name": "fun_friday_view", "frequency": "every_friday", "active": True},
    {"name": "Pago Mensual", "url_name": "payment_reminder_form", "frequency": "monthly_day_1", "active": True},
    {"name": "Vacaciones", "url_name": "vacation_closure_form", "frequency": "manual", "active": True},
    {"name": "Certificado Renta", "url_name": "tax_certificate_form", "frequency": "yearly_april", "active": True},
    {"name": "Informe Mensual", "url_name": "monthly_report_form", "frequency": "monthly_last_day", "active": True},
    {"name": "Bienvenida", "url_name": "welcome_form", "frequency": "on_student_creation", "active": True},
    {"name": "Cumpleaños", "url_name": "birthday_form", "frequency": "daily", "active": True},
    {"name": "Recibos", "url_name": "receipts_form", "frequency": "quarterly", "active": True},
    {"name": "Matrículas", "url_name": "enrollment_form", "frequency": "on_enrollment", "active": True},
]

# ============================================================================
# SESSION KEYS
# ============================================================================
# Lives here, in a module that imports NOTHING, because it is the only thing
# `core.views.auth` ever needed from `core.views.two_factor` — and that single
# import was the whole of a CodeQL-reported import cycle (alerts 601/602 on
# PR #77): auth imported two_factor at module level for this string, and
# two_factor had to import auth back lazily to avoid a circular import at
# URL-conf load. One shared constant in a leaf breaks the cycle at its source
# instead of tolerating it, the same reasoning as `billing/money.py` and
# `core/date_utils.py`.
#
# A session with only this key set is NOT logged in — it is mid-2FA. The
# `is_authenticated` gate in SimpleAuthMiddleware is what says otherwise.
PENDING_2FA_SESSION_KEY = "_2fa_pending_user_id"
