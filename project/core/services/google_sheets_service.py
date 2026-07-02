"""
Google Sheets export service (v1.2).

Uses a service-account JSON credential + target spreadsheet ID from settings.
When either is missing the service reports itself as unconfigured; callers must
check `is_configured()` before invoking any export method.

The credential can be supplied in two forms:
  1. A path to a JSON file (`GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE`)
  2. Inline JSON (`GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON`) — recommended for
     Cloud Run + Secret Manager where mounting files is awkward.

Both forms are read from Django settings, which in turn pick them up from
environment variables. Missing / malformed credentials never raise on import;
the failure surfaces only when an export is actually attempted.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from billing.models import Payment, current_academic_year
from students.models import Student

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


@dataclass
class ExportResult:
    """Outcome of an export call — always safe to return; never raises."""

    success: bool
    worksheet: str
    rows_written: int = 0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "worksheet": self.worksheet,
            "rows_written": self.rows_written,
            "error": self.error,
        }


def _load_service_account_info() -> dict | None:
    """Return the service-account credential dict, or None if unset/malformed."""
    inline = getattr(settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON", "")
    if inline:
        try:
            return json.loads(inline)
        except json.JSONDecodeError as e:
            logger.warning("GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON is not valid JSON: %s", e)
            return None

    path = getattr(settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE", "")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except OSError as e:
            logger.warning("GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE unreadable: %s", e)
            return None
        except json.JSONDecodeError as e:
            logger.warning("GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE is not valid JSON: %s", e)
            return None

    return None


class GoogleSheetsService:
    """
    Thin wrapper around `gspread` targeted at "export a table to a worksheet"
    workflows. Kept intentionally focused — no read/formula logic, no batching
    across multiple spreadsheets, no formatting.
    """

    def __init__(self, spreadsheet_id: str | None = None):
        self.spreadsheet_id = spreadsheet_id or getattr(settings, "GOOGLE_SHEETS_SPREADSHEET_ID", "")
        self._client = None
        self._sheet = None

    # ── Configuration checks ─────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """True iff both a spreadsheet id and a credential are available."""
        return bool(self.spreadsheet_id) and _load_service_account_info() is not None

    def _get_sheet(self):
        """Lazy-open the target spreadsheet. Cached on the instance."""
        if self._sheet is not None:
            return self._sheet

        info = _load_service_account_info()
        if info is None:
            raise RuntimeError("Google service account credentials are not configured.")
        if not self.spreadsheet_id:
            raise RuntimeError("GOOGLE_SHEETS_SPREADSHEET_ID is not configured.")

        # Late import — google-auth is only needed once a real export runs, and
        # the transitive dependency footprint is heavy.
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
        self._client = gspread.authorize(creds)
        self._sheet = self._client.open_by_key(self.spreadsheet_id)
        return self._sheet

    def _get_or_create_worksheet(self, name: str, cols: int):
        sheet = self._get_sheet()
        try:
            ws = sheet.worksheet(name)
        except Exception:  # gspread.exceptions.WorksheetNotFound
            ws = sheet.add_worksheet(title=name, rows=100, cols=cols)
        return ws

    # ── Export methods ───────────────────────────────────────────────────────

    def export_students(self, worksheet_name: str = "Students") -> ExportResult:
        """
        Write one row per active student (+ header row). Overwrites the target
        worksheet's contents so the sheet is always an authoritative snapshot,
        never an append-only log.
        """
        try:
            headers = [
                "ID",
                "Nombre",
                "Apellidos",
                "Fecha nacimiento",
                "Edad",
                "Género",
                "Grupo",
                "Colegio",
                "Adulto",
                "GDPR",
                "En espera",
                "Alta",
                "Padres",
            ]
            students = (
                Student.objects.filter(active=True)
                .select_related("group")
                .prefetch_related("parents")
                .order_by("last_name", "first_name")
            )
            rows: list[list[Any]] = [headers]
            for s in students:
                parent_names = ", ".join(p.full_name for p in s.parents.all())
                rows.append(
                    [
                        s.id,
                        s.first_name,
                        s.last_name,
                        s.birth_date.isoformat() if s.birth_date else "",
                        s.age if s.birth_date else "",
                        s.get_gender_display(),
                        s.group.group_name if s.group_id else "",
                        s.school or "",
                        "Sí" if s.is_adult else "No",
                        "Sí" if s.gdpr_signed else "No",
                        "Sí" if s.is_waiting else "No",
                        s.created_at.strftime("%Y-%m-%d") if s.created_at else "",
                        parent_names,
                    ]
                )

            ws = self._get_or_create_worksheet(worksheet_name, cols=len(headers))
            ws.clear()
            ws.update(rows, "A1")
            return ExportResult(success=True, worksheet=worksheet_name, rows_written=len(rows) - 1)
        except Exception as e:  # noqa: BLE001 — never re-raise; caller wants a result object
            logger.exception("export_students failed")
            return ExportResult(success=False, worksheet=worksheet_name, error=str(e))

    def export_payments(
        self,
        worksheet_name: str = "Payments",
        academic_year: str | None = None,
    ) -> ExportResult:
        """
        Write one row per payment (+ header row). Filterable by academic_year;
        defaults to the current year.
        """
        try:
            year = academic_year or current_academic_year()
            headers = [
                "ID",
                "Estudiante",
                "Padre/Tutor",
                "Concepto",
                "Tipo",
                "Método",
                "Importe (€)",
                "Estado",
                "Fecha cobro",
                "Vencimiento",
                "Año académico",
            ]
            payments = (
                Payment.objects.filter(enrollment__academic_year=year)
                .select_related("student", "parent", "enrollment")
                .order_by("-due_date", "student__last_name")
            )
            rows: list[list[Any]] = [headers]
            for p in payments:
                rows.append(
                    [
                        p.id,
                        p.student.full_name if p.student_id else "",
                        p.parent.full_name if p.parent_id else "",
                        p.concept or "",
                        p.get_payment_type_display(),
                        p.get_payment_method_display(),
                        str(p.amount),
                        p.get_payment_status_display(),
                        p.payment_date.isoformat() if p.payment_date else "",
                        p.due_date.isoformat() if p.due_date else "",
                        p.enrollment.academic_year if p.enrollment_id else "",
                    ]
                )

            ws = self._get_or_create_worksheet(worksheet_name, cols=len(headers))
            ws.clear()
            ws.update(rows, "A1")
            return ExportResult(success=True, worksheet=worksheet_name, rows_written=len(rows) - 1)
        except Exception as e:  # noqa: BLE001
            logger.exception("export_payments failed")
            return ExportResult(success=False, worksheet=worksheet_name, error=str(e))


def get_service() -> GoogleSheetsService:
    """Convenience constructor used from views / management commands / tasks."""
    return GoogleSheetsService()


__all__ = [
    "ExportResult",
    "GoogleSheetsService",
    "get_service",
]
