r"""Spanish date/number formats for Five a Day — dd/mm/yyyy everywhere.

Django's localization always wins over the `DATE_FORMAT` / `SHORT_DATE_FORMAT`
settings: with `LANGUAGE_CODE = "es-es"` it reads
`django.conf.locale.es.formats`, whose `DATE_FORMAT` is `j \d\e F \d\e Y`.
So a bare `{{ payment.due_date }}` rendered "31 de agosto de 2026" while every
template that bothered to write `|date:"d/m/Y"` rendered "31/08/2026" — the same
page showing a date two different ways, and settings that looked correct but did
nothing.

`FORMAT_MODULE_PATH` (in settings) points here, which is the only supported way
to override a locale's formats since `USE_L10N = False` was removed in Django 5.
Keep this file and the `DATE_FORMAT` settings in agreement.
"""

DATE_FORMAT = "d/m/Y"
SHORT_DATE_FORMAT = "d/m/Y"
DATETIME_FORMAT = "d/m/Y H:i"
SHORT_DATETIME_FORMAT = "d/m/Y H:i"
YEAR_MONTH_FORMAT = "m/Y"
MONTH_DAY_FORMAT = "d/m"
TIME_FORMAT = "H:i"

# Input parsing is unchanged from Django's es locale, plus the ISO form the
# HTML5 date widget posts — the enrollment forms already accept both.
DATE_INPUT_FORMATS = ["%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"]
DATETIME_INPUT_FORMATS = [
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
]

DECIMAL_SEPARATOR = ","
THOUSAND_SEPARATOR = "."
NUMBER_GROUPING = 3
