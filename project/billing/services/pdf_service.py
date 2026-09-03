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
_GRID_COLOR = colors.HexColor("#DDDDDD")
_LABEL_BG = colors.HexColor("#F5F5F5")
_TOTAL_BG = colors.HexColor("#EEEEEE")

#: Printable width in millimetres: A4 is 210 mm and `_build_pdf` takes 18 mm off
#: each side, so a table has 174 mm. Two tables declared more than that — the
#: payment history 186 mm and the report's group table 175 mm — and reportlab
#: does not refuse them: the surplus is drawn past the right edge of the page,
#: so the last column ("Importe (€)", "En espera") was cut off on print. Every
#: table goes through `_fit_widths` now, which is the only reason the over-run
#: is visible at all.
_FRAME_WIDTH_MM = 174


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
    to hard-coded defaults so a fresh install still produces a valid document.

    The five `academy_*` fields are REAL columns as of v1.27.1, editable from
    /management/. Until then they did not exist, every `getattr` fell through to
    the default, and `cif` defaults to blank — so the CIF was blank on every tax
    certificate the academy had ever issued, on a document that asserts IRPF
    deductibility. The `or <default>` is kept deliberately: a field an admin has
    not filled in yet must still yield a usable document rather than an empty
    letterhead.
    """
    try:
        from billing.models import SiteConfiguration

        config = SiteConfiguration.get_config()
        return AcademyInfo(
            name=config.academy_name or AcademyInfo.name,
            address=config.academy_address or AcademyInfo.address,
            cif=config.academy_cif or AcademyInfo.cif,
            phone=config.academy_phone or AcademyInfo.phone,
            website=config.academy_website or AcademyInfo.website,
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
    """Build the top-of-document header (academy name + title + subtitle).

    The CIF is printed whenever it is populated — a fiscal certificate that does
    not name the issuer's tax id is not much use to the family filing with it,
    and `_get_academy_info` has always been ready to read one. It is omitted
    rather than printed empty when the field is blank.

    Every `academy.*` value goes through `_md()`: they come from
    SiteConfiguration (free text an admin types) and land in a `Paragraph`,
    which parses mini-HTML — an academy name containing `&` or `<` would raise
    `paraparser: syntax error` and kill every document at once.
    """
    contact = f"{_md(academy.address)} · Tel {_md(academy.phone)}"
    if academy.cif:
        contact = f"{contact} · CIF {_md(academy.cif)}"
    return [
        Paragraph(_md(academy.name), styles["title"]),
        Paragraph(contact, styles["subtitle"]),
        Paragraph(title, styles["h2"]),
        Paragraph(subtitle, styles["body"]),
        Spacer(1, 6 * mm),
    ]


def _footer_flowables(styles: dict[str, ParagraphStyle], academy: AcademyInfo, extra_legal: str = ""):
    today = date.today().strftime("%d/%m/%Y")
    parts = [
        Spacer(1, 8 * mm),
        Paragraph(
            f"Documento generado automáticamente el {today}. {_md(academy.website)}",
            styles["footer"],
        ),
    ]
    if extra_legal:
        parts.insert(0, Paragraph(extra_legal, styles["footer"]))
    return parts


def _fit_widths(widths_mm) -> list[float]:
    """Convert millimetre column widths to points, scaled into the frame.

    See `_FRAME_WIDTH_MM`: a table wider than the frame is not refused, it is
    drawn off the edge of the page, so the bug shows up as a missing column on
    paper and nowhere else. The declared widths below all fit; this is the net
    that keeps the next edit from re-introducing the same silent clipping.
    """
    widths = list(widths_mm)
    total = sum(widths)
    if total > _FRAME_WIDTH_MM:
        scale = _FRAME_WIDTH_MM / total
        widths = [w * scale for w in widths]
    return [w * mm for w in widths]


def _grid_table(
    rows,
    widths_mm,
    *,
    header_row: bool = True,
    label_column: bool = False,
    total_row: bool = False,
    right_align: int | None = None,
    font_size: int = 9,
    repeat_header: bool = False,
    valign_middle: bool = False,
) -> Table:
    """The bordered data table every document in this module draws.

    The same `TableStyle` block was written out five times with small
    divergences, which is how one of them ended up 12 mm wider than the page and
    another 1 mm wider — nobody was comparing them. The knobs are the real
    differences between the five:

    * `header_row`  — first row is a violet band with white bold text.
    * `label_column`— first column is a grey bold label (the key/value tables).
    * `total_row`   — last row shaded, bold from the third column on (TOTAL /
                      Subtotal lines).
    * `right_align` — index of the money column, right-aligned.
    * `repeat_header` / `valign_middle` / `font_size` — per-document trim.
    """
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.25, _GRID_COLOR),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4 if header_row else 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4 if header_row else 5),
    ]
    if header_row:
        commands += [
            ("BACKGROUND", (0, 0), (-1, 0), _PRIMARY_COLOR),
            ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_TEXT_COLOR),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    if label_column:
        commands += [
            ("BACKGROUND", (0, 0), (0, -1), _LABEL_BG),
            ("TEXTCOLOR", (0, 0), (0, -1), _MUTED_COLOR),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ]
    if total_row:
        commands += [
            ("BACKGROUND", (0, -1), (-1, -1), _TOTAL_BG),
            ("FONTNAME", (2, -1), (-1, -1), "Helvetica-Bold"),
        ]
    if right_align is not None:
        commands.append(("ALIGN", (right_align, 0), (right_align, -1), "RIGHT"))
    if valign_middle:
        commands.append(("VALIGN", (0, 0), (-1, -1), "MIDDLE"))

    table = Table(rows, colWidths=_fit_widths(widths_mm), repeatRows=1 if repeat_header else 0)
    table.setStyle(TableStyle(commands))
    return table


def _banner_table(rows, widths_mm, *, font_size: int = 12, shade_from: int | None = None) -> Table:
    """The solid violet TOTAL banner (one or two rows).

    `shade_from` greys every row from that index on, which is how the payment
    history distinguishes "TOTAL COBRADO" from "PENDIENTE" — the two must never
    read as one figure.
    """
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), _PRIMARY_COLOR),
        ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_TEXT_COLOR),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
    ]
    if shade_from is not None:
        commands.append(("BACKGROUND", (0, shade_from), (-1, -1), _TOTAL_BG))
    table = Table(rows, colWidths=_fit_widths(widths_mm))
    table.setStyle(TableStyle(commands))
    return table


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

    info_table = _grid_table(body_rows, [55, 105], header_row=False, label_column=True, font_size=10)
    total_table = _banner_table([["TOTAL", f"{payment.amount:.2f} €"]], [120, 40])

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

    table = _grid_table(rows, [25, 80, 35, 25], total_row=True, right_align=3)

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

    # 44+22+21+21+22+22+22 = 174 mm, the exact printable width. This was
    # 52+22+22+22+24+22+22 = 186 mm — 12 mm past the right edge of an A4 page,
    # so "Importe (€)" (the one column the reader is looking for) was the column
    # that fell off. `Concepto` absorbs the difference: it is the only free-text
    # column and it wraps.
    table = _grid_table(
        rows,
        [44, 22, 21, 21, 22, 22, 22],
        right_align=6,
        font_size=8,
        repeat_header=True,
        valign_middle=True,
    )

    totals = _banner_table(
        [
            ["TOTAL COBRADO", f"{total_paid:.2f} €"],
            ["PENDIENTE", f"{total_pending:.2f} €"],
        ],
        [120, 40],
        font_size=11,
        shade_from=1,
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

    # Grouped by student ID, not by NAME. Two siblings called the same thing —
    # the academy has had them, and a re-registered student can share a name with
    # a cousin — were merged into ONE block with ONE subtotal, on a document the
    # family files with the tax authority. The name is carried in the entry for
    # the heading; the key is the thing that identifies a person.
    students_data: dict[int | None, dict] = {}
    total_year = Decimal("0.00")
    for p in payments:
        entry = students_data.setdefault(
            p.student_id,
            {
                "name": p.student.full_name if p.student_id else "Sin estudiante",
                "payments": [],
                "total": Decimal("0.00"),
            },
        )
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
        for entry in students_data.values():
            flowables.append(Paragraph(f"<b>Estudiante:</b> {_md(entry['name'])}", styles["h2"]))

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

            flowables.append(_grid_table(rows, [25, 80, 35, 25], total_row=True, right_align=3))
            flowables.append(Spacer(1, 5 * mm))

        flowables.append(_banner_table([[f"TOTAL PAGADO EN {year}", f"{total_year:.2f} €"]], [130, 40]))

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
        return _grid_table(rows, [70, 60], header_row=False, label_column=True, font_size=10)

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
        # 45+55+53+21 = 174 mm. It was 175 — one millimetre past the frame, so
        # the "En espera" column printed clipped on the right edge. It also had
        # no `TableStyle` at all: the only table in the module rendered without
        # a header band or a grid, which read as a rendering fault.
        _grid_table(group_rows, [45, 55, 53, 21], font_size=9, right_align=3),
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
