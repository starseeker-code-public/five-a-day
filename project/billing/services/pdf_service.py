"""
PDF generation service (v1.3).

Produces payment receipts, quarterly summaries, and tax certificates using
reportlab (pure Python — no native cairo/pango dependencies to install on the
Cloud Run image or the testing VM).

All functions return raw PDF bytes so callers can:
  - Attach to email (comms/tax_certificate flow)
  - Stream as HTTP response (billing/receipt view)
  - Persist to Cloud Storage (future)

The `AcademyInfo` dataclass captures the header block on every document —
pulled from SiteConfiguration where possible, with hard-coded fallbacks so
the service still produces sensible output on a fresh install.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_PRIMARY_COLOR = colors.HexColor("#4F46E5")
_HEADER_TEXT_COLOR = colors.white
_MUTED_COLOR = colors.HexColor("#666666")


@dataclass(frozen=True)
class AcademyInfo:
    name: str = "Five a Day English Academy"
    address: str = "C/ Hermanos Jiménez 25 · 02004 Albacete"
    cif: str = ""
    phone: str = "967 049 096"
    website: str = "www.fiveadayenglish.com"


def _md(value) -> str:
    """Escape text for a reportlab `Paragraph`.

    Paragraph parses a mini-HTML dialect, so raw names went in as MARKUP: a
    student called `O<Brien` raised `ValueError: paraparser: syntax error:
    parse ended with 1 unclosed tags` and killed PDF generation outright, while
    `<b>x</b>` silently rendered as bold. Table cells are plain strings and
    don't need this, but anything reaching a Paragraph does.
    """
    return str("" if value is None else value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _get_academy_info() -> AcademyInfo:
    """Pull business info from SiteConfiguration when populated, fall back
    to hard-coded defaults so a fresh install still produces a valid document."""
    try:
        from billing.models import SiteConfiguration

        config = SiteConfiguration.get_config()
        return AcademyInfo(
            name=getattr(config, "academy_name", "") or AcademyInfo.name,
            address=getattr(config, "academy_address", "") or AcademyInfo.address,
            cif=getattr(config, "academy_cif", "") or AcademyInfo.cif,
            phone=getattr(config, "academy_phone", "") or AcademyInfo.phone,
            website=getattr(config, "academy_website", "") or AcademyInfo.website,
        )
    except Exception:  # noqa: BLE001 — fallback on any config error, always return usable info
        return AcademyInfo()


def _styles() -> dict[str, ParagraphStyle]:
    """Reusable paragraph styles keyed by role."""
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title",
            parent=ss["Heading1"],
            textColor=_PRIMARY_COLOR,
            alignment=1,  # center
            fontSize=18,
            spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=ss["Normal"],
            textColor=_MUTED_COLOR,
            alignment=1,
            fontSize=10,
            spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=ss["Heading2"],
            textColor=_PRIMARY_COLOR,
            fontSize=13,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body",
            parent=ss["Normal"],
            fontSize=10,
            leading=13,
        ),
        "footer": ParagraphStyle(
            "footer",
            parent=ss["Normal"],
            fontSize=8,
            textColor=_MUTED_COLOR,
            alignment=1,
        ),
    }


def _header_flowables(styles: dict[str, ParagraphStyle], academy: AcademyInfo, title: str, subtitle: str):
    """Build the top-of-document header (academy name + title + subtitle)."""
    return [
        Paragraph(academy.name, styles["title"]),
        Paragraph(f"{academy.address} · Tel {academy.phone}", styles["subtitle"]),
        Paragraph(title, styles["h2"]),
        Paragraph(subtitle, styles["body"]),
        Spacer(1, 6 * mm),
    ]


def _footer_flowables(styles: dict[str, ParagraphStyle], academy: AcademyInfo, extra_legal: str = ""):
    today = date.today().strftime("%d/%m/%Y")
    parts = [
        Spacer(1, 8 * mm),
        Paragraph(
            f"Documento generado automáticamente el {today}. {academy.website}",
            styles["footer"],
        ),
    ]
    if extra_legal:
        parts.insert(0, Paragraph(extra_legal, styles["footer"]))
    return parts


def _build_pdf(flowables: Iterable) -> bytes:
    """Render the given flowables to a byte buffer and return the PDF bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Five a Day",
    )
    doc.build(list(flowables))
    return buffer.getvalue()


# ── Public entry points ─────────────────────────────────────────────────────


def generate_payment_receipt(payment) -> bytes:
    """Single-payment receipt (recibo). Suitable for email attachment or download."""
    academy = _get_academy_info()
    styles = _styles()

    student = payment.student
    parent = payment.parent
    concept = payment.concept or payment.get_payment_type_display()
    paid_at = payment.payment_date.strftime("%d/%m/%Y") if payment.payment_date else "—"
    due_at = payment.due_date.strftime("%d/%m/%Y") if payment.due_date else "—"

    body_rows = [
        ["Recibo Nº", str(payment.id)],
        ["Fecha emisión", date.today().strftime("%d/%m/%Y")],
        ["Fecha cobro", paid_at],
        ["Fecha vencimiento", due_at],
        ["Titular", parent.full_name if parent else "—"],
        ["DNI titular", parent.dni if parent else "—"],
        ["Estudiante", student.full_name if student else "—"],
        ["Concepto", concept],
        ["Método de pago", payment.get_payment_method_display()],
        ["Estado", payment.get_payment_status_display()],
        ["Importe", f"{payment.amount:.2f} €"],
    ]

    info_table = Table(body_rows, colWidths=[55 * mm, 105 * mm])
    info_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F5F5F5")),
                ("TEXTCOLOR", (0, 0), (0, -1), _MUTED_COLOR),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
            ]
        )
    )

    total_table = Table(
        [["TOTAL", f"{payment.amount:.2f} €"]],
        colWidths=[120 * mm, 40 * mm],
    )
    total_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _PRIMARY_COLOR),
                ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_TEXT_COLOR),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 12),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ("TOPPADDING", (0, 0), (-1, 0), 8),
            ]
        )
    )

    flowables = [
        *_header_flowables(
            styles,
            academy,
            f"RECIBO Nº {payment.id}",
            f"Emitido a nombre de {_md(parent.full_name if parent else student.full_name)}",
        ),
        info_table,
        Spacer(1, 8 * mm),
        total_table,
        *_footer_flowables(styles, academy),
    ]
    return _build_pdf(flowables)


def generate_quarterly_summary(student, payments, quarter_label: str) -> bytes:
    """
    Quarterly summary for a single student. `payments` is any iterable of
    Payment records (typically completed only). `quarter_label` is what the
    caller wants to show — e.g., "Q1 2026" or "Enero–Marzo 2026".
    """
    academy = _get_academy_info()
    styles = _styles()

    rows: list[list[str]] = [["Fecha", "Concepto", "Tipo", "Importe (€)"]]
    total = Decimal("0.00")
    for p in payments:
        d = (p.payment_date or p.due_date).strftime("%d/%m/%Y") if (p.payment_date or p.due_date) else "—"
        rows.append([d, p.concept or "—", p.get_payment_type_display(), f"{p.amount:.2f}"])
        total += p.amount
    rows.append(["", "", "TOTAL", f"{total:.2f}"])

    table = Table(rows, colWidths=[25 * mm, 80 * mm, 35 * mm, 25 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _PRIMARY_COLOR),
                ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_TEXT_COLOR),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (2, -1), (-1, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EEEEEE")),
                ("ALIGN", (3, 0), (3, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    flowables = [
        *_header_flowables(
            styles,
            academy,
            f"RESUMEN TRIMESTRAL · {_md(quarter_label)}",
            f"Estudiante: {_md(student.full_name)}",
        ),
        table,
        *_footer_flowables(styles, academy),
    ]
    return _build_pdf(flowables)


def generate_student_payment_history(student, payments, *, title_suffix: str = "") -> bytes:
    """Full payment history for one student — what was paid, when and how.

    `payments` is any iterable of Payment rows (the caller decides the filter).
    Shows the payment method and both dates so the academy can answer "cuándo
    lo pagó y cómo lo pagó" from a single sheet, and totals the collected vs
    outstanding amounts separately so the two are never conflated.
    """
    academy = _get_academy_info()
    styles = _styles()

    rows: list[list[str]] = [["Concepto", "Tipo", "Vencimiento", "Fecha cobro", "Método", "Estado", "Importe (€)"]]
    total_paid = Decimal("0.00")
    total_pending = Decimal("0.00")

    payments = list(payments)
    for p in payments:
        rows.append(
            [
                # NOT _md(): this is a plain Table cell, which reportlab draws
                # verbatim with drawString — no mini-HTML parsing. Escaping here
                # printed the entity itself, so a concept like "Clases & material"
                # rendered as "Clases &amp; material". Only Paragraph needs _md.
                # generate_quarterly_summary above and generate_tax_certificate
                # below already write this same field raw; this was the odd one out.
                p.concept or "—",
                p.get_payment_type_display(),
                p.due_date.strftime("%d/%m/%Y") if p.due_date else "—",
                p.payment_date.strftime("%d/%m/%Y") if p.payment_date else "—",
                p.get_payment_method_display() if p.payment_status == "completed" else "—",
                p.get_payment_status_display(),
                f"{p.amount:.2f}",
            ]
        )
        if p.payment_status == "completed":
            total_paid += p.amount
        elif p.payment_status == "pending":
            total_pending += p.amount

    if not payments:
        rows.append(["Sin pagos registrados", "—", "—", "—", "—", "—", "0.00"])

    table = Table(rows, colWidths=[52 * mm, 22 * mm, 22 * mm, 22 * mm, 24 * mm, 22 * mm, 22 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _PRIMARY_COLOR),
                ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_TEXT_COLOR),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (6, 0), (6, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )

    totals = Table(
        [
            ["TOTAL COBRADO", f"{total_paid:.2f} €"],
            ["PENDIENTE", f"{total_pending:.2f} €"],
        ],
        colWidths=[120 * mm, 40 * mm],
    )
    totals.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _PRIMARY_COLOR),
                ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_TEXT_COLOR),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#EEEEEE")),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )

    group_name = student.group.group_name if getattr(student, "group_id", None) else "Sin grupo"
    subtitle = f"Estudiante: {_md(student.full_name)} · Grupo: {_md(group_name)}"
    heading = "HISTORIAL DE PAGOS"
    if title_suffix:
        heading = f"{heading} · {title_suffix}"

    flowables = [
        *_header_flowables(styles, academy, heading, subtitle),
        table,
        Spacer(1, 8 * mm),
        totals,
        *_footer_flowables(styles, academy),
    ]
    return _build_pdf(flowables)


def generate_tax_certificate(parent, year: int) -> bytes:
    """
    Fiscal certificate — full-year snapshot of every completed payment made by
    `parent`, grouped by student. Replaces the HTML-fallback path in
    `comms/services/email_functions.generate_tax_certificate_pdf`.
    """
    from billing.models import Payment

    academy = _get_academy_info()
    styles = _styles()

    payments = (
        Payment.objects.filter(parent=parent, payment_status="completed", payment_date__year=year)
        .select_related("student")
        .order_by("student__last_name", "payment_date")
    )

    students_data: dict[str, dict] = {}
    total_year = Decimal("0.00")
    for p in payments:
        key = p.student.full_name if p.student_id else "Sin estudiante"
        entry = students_data.setdefault(key, {"payments": [], "total": Decimal("0.00")})
        entry["payments"].append(p)
        entry["total"] += p.amount
        total_year += p.amount

    flowables = [
        *_header_flowables(
            styles,
            academy,
            f"CERTIFICADO FISCAL · AÑO {year}",
            f"Titular: {_md(parent.full_name)} · DNI: {_md(parent.dni)}",
        ),
    ]

    if not students_data:
        flowables.append(
            Paragraph(
                f"No consta ningún pago completado a nombre de {_md(parent.full_name)} durante {year}.",
                styles["body"],
            )
        )
    else:
        for student_name, entry in students_data.items():
            flowables.append(Paragraph(f"<b>Estudiante:</b> {_md(student_name)}", styles["h2"]))

            rows: list[list[str]] = [["Fecha", "Concepto", "Tipo", "Importe (€)"]]
            for p in entry["payments"]:
                rows.append(
                    [
                        p.payment_date.strftime("%d/%m/%Y") if p.payment_date else "—",
                        p.concept or "—",
                        p.get_payment_type_display(),
                        f"{p.amount:.2f}",
                    ]
                )
            rows.append(["", "", "Subtotal", f"{entry['total']:.2f}"])

            table = Table(rows, colWidths=[25 * mm, 80 * mm, 35 * mm, 25 * mm])
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), _PRIMARY_COLOR),
                        ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_TEXT_COLOR),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTNAME", (2, -1), (-1, -1), "Helvetica-Bold"),
                        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EEEEEE")),
                        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            flowables.append(table)
            flowables.append(Spacer(1, 5 * mm))

        grand = Table([[f"TOTAL PAGADO EN {year}", f"{total_year:.2f} €"]], colWidths=[130 * mm, 40 * mm])
        grand.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), _PRIMARY_COLOR),
                    ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_TEXT_COLOR),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 12),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                    ("TOPPADDING", (0, 0), (-1, 0), 8),
                ]
            )
        )
        flowables.append(grand)

    flowables.extend(
        _footer_flowables(
            styles,
            academy,
            extra_legal=(
                "Documento con validez a efectos de la declaración del IRPF. "
                "Los importes corresponden a gastos de enseñanza de idiomas."
            ),
        )
    )
    return _build_pdf(flowables)


def generate_report_pdf(report: dict, month: int, year: int) -> bytes:
    """
    v1.7 report snapshot as a PDF. `report` is the dict returned by
    `core.services.analytics_service.dashboard_report(month, year)`.

    Kept in the service layer (rather than the view) so both the download
    endpoint and any future async cron job that emails the report can
    share the same code path.
    """
    academy = _get_academy_info()
    styles = _styles()

    def money(v):
        return f"{v:.2f} €"

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
    return _build_pdf(flowables)


__all__ = [
    "AcademyInfo",
    "generate_payment_receipt",
    "generate_quarterly_summary",
    "generate_report_pdf",
    "generate_tax_certificate",
]
