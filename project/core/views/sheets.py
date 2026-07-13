"""Google Sheets export endpoints (v1.2)."""

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from core.models import HistoryLog
from core.services.google_sheets_service import get_service


@require_http_methods(["POST"])
def export_to_sheets(request):
    """
    Trigger a Sheets export from the UI. Accepts ?target=students|payments|both.
    Returns 503 when the integration isn't configured so the frontend can
    surface a specific message rather than a generic error.
    """
    target = (request.POST.get("target") or "both").strip().lower()
    if target not in {"students", "payments", "both"}:
        return JsonResponse(
            {"success": False, "error": "target debe ser students, payments o both."},
            status=400,
        )

    service = get_service()
    if not service.is_configured():
        return JsonResponse(
            {
                "success": False,
                "error": (
                    "Integración con Google Sheets no configurada. "
                    "Define GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON (o _FILE) "
                    "y GOOGLE_SHEETS_SPREADSHEET_ID en el entorno."
                ),
            },
            status=503,
        )

    results = []
    if target in {"students", "both"}:
        results.append(service.export_students())
    if target in {"payments", "both"}:
        results.append(service.export_payments())

    overall_success = all(r.success for r in results)
    total_rows = sum(r.rows_written for r in results if r.success)

    if overall_success:
        HistoryLog.log(
            "sheets_exported",
            f"Exportación a Google Sheets: {target} — {total_rows} filas.",
            icon="cloud_upload",
        )

    return JsonResponse(
        {
            "success": overall_success,
            "target": target,
            "results": [r.as_dict() for r in results],
        },
        status=200 if overall_success else 502,
    )


__all__ = ["export_to_sheets"]
