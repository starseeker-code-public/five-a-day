"""Reports & analytics dashboard (v1.7)."""

from datetime import date

from django.http import HttpResponse
from django.shortcuts import render

from core.decorators import admin_required
from core.services.analytics_service import dashboard_report
from core.utils import MAX_QUERY_YEAR, MIN_QUERY_YEAR, safe_int


def _parse_month_year(request):
    """Month/year from the query string, via the shared `safe_int`.

    This was the third hand-rolled copy of that helper — the one `safe_int`'s
    own docstring says it consolidated — and its semantics were OPPOSITE:
    it clamped where the others reject, so `?month=13` rendered December here
    and the current month everywhere else, and `?year=99999` rendered a
    dashboard for the year 9999. Out-of-range now falls back to today, and
    the valid year range is the same pair of constants every other view uses.
    """
    today = date.today()
    month = safe_int(request.GET.get("month"), default=today.month, low=1, high=12)
    year = safe_int(request.GET.get("year"), default=today.year, low=MIN_QUERY_YEAR, high=MAX_QUERY_YEAR)
    return month, year


@admin_required
def reports_view(request):
    """Full-page report dashboard with month/year controls."""
    month, year = _parse_month_year(request)

    return render(
        request,
        "reports.html",
        {
            "report": dashboard_report(month, year),
            "month": month,
            "year": year,
        },
    )


@admin_required
def reports_pdf(request):
    """Download the current report as a reportlab-rendered PDF.

    Thin view: delegates rendering to the pdf_service so the same code path
    is used from any future cron job that emails the report.
    """
    from billing.services.pdf_service import generate_report_pdf

    month, year = _parse_month_year(request)
    report = dashboard_report(month, year)
    pdf_bytes = generate_report_pdf(report, month, year)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="informe-{year}-{month:02d}.pdf"'
    return response


__all__ = ["reports_view", "reports_pdf"]
