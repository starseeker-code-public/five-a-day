"""Reports & analytics dashboard (v1.7)."""

from datetime import date

from django.http import HttpResponse
from django.shortcuts import render

from core.services.analytics_service import dashboard_report


def _parse_month_year(request):
    today = date.today()
    try:
        month = int(request.GET.get("month") or today.month)
        year = int(request.GET.get("year") or today.year)
    except (TypeError, ValueError):
        month, year = today.month, today.year
    # Clamp month to 1-12 — anything outside makes queries return empty and
    # renders a confusing dashboard.
    month = max(1, min(month, 12))
    return month, year


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
