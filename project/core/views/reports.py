"""Reports & analytics dashboard (v1.7)."""

from datetime import date

from django.http import HttpResponse
from django.shortcuts import render

from core.services.analytics_service import dashboard_report

# noqa: I001 — organised for readability, not by module boundary


def reports_view(request):
    """Full-page report dashboard with month/year controls."""
    today = date.today()
    try:
        month = int(request.GET.get("month") or today.month)
        year = int(request.GET.get("year") or today.year)
    except (TypeError, ValueError):
        month, year = today.month, today.year

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
    """Download the current report as a reportlab-rendered PDF."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from billing.services.pdf_service import _footer_flowables, _get_academy_info, _header_flowables, _styles

    today = date.today()
    try:
        month = int(request.GET.get("month") or today.month)
        year = int(request.GET.get("year") or today.year)
    except (TypeError, ValueError):
        month, year = today.month, today.year

    report = dashboard_report(month, year)
    academy = _get_academy_info()
    styles = _styles()

    money = lambda v: f"{v:.2f} €"  # noqa: E731 — tiny inline formatter, no need for a def

    financial_rows = [
        ["Ingresos", money(report["current_month"]["income"])],
        ["Pendiente", money(report["current_month"]["pending"])],
        ["Gastos", money(report["current_month"]["expenses"])],
        ["Beneficio neto", money(report["current_month"]["net"])],
    ]
    collection_rows = [
        ["Esperado", money(report["collection"]["expected"])],
        ["Cobrado", money(report["collection"]["collected"])],
        ["Tasa de cobro", f"{report['collection']['percent']}%"],
    ]
    retention_rows = [
        ["Estudiantes con 1+ años", str(report["retention"]["baseline"])],
        ["Aún activos", str(report["retention"]["still_active"])],
        ["Retención", f"{report['retention']['retention_percent']}%"],
    ]
    group_rows = [["Grupo", "Profesor", "Ocupación", "En espera"]] + [
        [
            row["name"],
            row["teacher"],
            (
                f"{row['enrolled']}/{row['max_students']} ({row['utilisation_percent']}%)"
                if row["max_students"]
                else f"{row['enrolled']} (sin cupo)"
            ),
            str(row["waiting"]),
        ]
        for row in report["groups"]
    ]

    def _kv_table(rows):
        t = Table(rows, colWidths=[70 * mm, 60 * mm])
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F5F5F5")),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return t

    flowables = [
        *_header_flowables(
            styles,
            academy,
            f"INFORME · {month:02d}/{year}",
            "Resumen financiero, tasa de cobro, retención y ocupación de grupos.",
        ),
        Paragraph("<b>Resumen financiero</b>", styles["h2"]),
        _kv_table(financial_rows),
        Spacer(1, 6 * mm),
        Paragraph("<b>Tasa de cobro</b>", styles["h2"]),
        _kv_table(collection_rows),
        Spacer(1, 6 * mm),
        Paragraph("<b>Retención de estudiantes</b>", styles["h2"]),
        _kv_table(retention_rows),
        Spacer(1, 6 * mm),
        Paragraph("<b>Ocupación por grupo</b>", styles["h2"]),
        Table(group_rows, colWidths=[45 * mm, 55 * mm, 55 * mm, 20 * mm]),
        *_footer_flowables(styles, academy),
    ]

    from io import BytesIO

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm
    )
    doc.build(flowables)

    response = HttpResponse(buf.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="informe-{year}-{month:02d}.pdf"'
    return response


__all__ = ["reports_view", "reports_pdf"]
