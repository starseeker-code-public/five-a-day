import json
import logging
from datetime import datetime

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from core.models import HistoryLog, TodoItem

logger = logging.getLogger(__name__)

# Mirrors TodoItem.text's max_length.
_TODO_TEXT_MAX = 500
_HISTORY_PAGE_SIZE = 20


@require_http_methods(["POST"])
def create_todo(request):
    try:
        data = json.loads(request.body)
        text = data.get("text", "").strip()
        due_date_str = data.get("due_date", "")

        if not text:
            return JsonResponse({"success": False, "error": "El texto no puede estar vacío"}, status=400)
        if not due_date_str:
            return JsonResponse({"success": False, "error": "La fecha es obligatoria"}, status=400)
        # TodoItem.text is varchar(500); an over-long value used to raise a
        # DataError that escaped the except clause below and 500'd.
        if len(text) > _TODO_TEXT_MAX:
            return JsonResponse(
                {"success": False, "error": f"El texto no puede superar los {_TODO_TEXT_MAX} caracteres."},
                status=400,
            )

        due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
        todo = TodoItem.objects.create(text=text, due_date=due_date)

        return JsonResponse(
            {
                "success": True,
                "todo": {
                    "id": todo.id,
                    "text": todo.text,
                    "due_date_iso": todo.due_date.isoformat(),
                    "due_date_display": todo.due_date.strftime("%d/%m/%Y"),
                    "is_overdue": todo.is_overdue,
                },
            }
        )
    except (ValueError, json.JSONDecodeError):
        logger.exception("Error creating todo")
        return JsonResponse({"success": False, "error": "Datos de tarea inválidos."}, status=400)


@require_http_methods(["POST"])
def complete_todo(request, todo_id):
    todo = get_object_or_404(TodoItem, id=todo_id)
    preview = todo.text[:50] + ("..." if len(todo.text) > 50 else "")
    todo.delete()
    HistoryLog.log("todo_completed", f'Tarea completada: "{preview}"', icon="task_alt")
    return JsonResponse({"success": True})


def history_list(request):
    from django.utils.timesince import timesince

    try:
        offset = int(request.GET.get("offset", 0))
    except (ValueError, TypeError):
        offset = 0
    # Django raises ValueError on a negative slice, so `?offset=-1` 500'd.
    offset = max(0, offset)
    limit = _HISTORY_PAGE_SIZE
    entries = HistoryLog.objects.all()[offset : offset + limit]

    data = []
    for entry in entries:
        data.append(
            {
                "id": entry.id,
                "action": entry.action,
                "action_display": entry.get_action_display(),
                "message": entry.message,
                "icon": entry.icon,
                "created_at": entry.created_at.isoformat(),
                "time_ago": timesince(entry.created_at) + " ago",
            }
        )

    return JsonResponse(
        {
            "entries": data,
            "has_more": HistoryLog.objects.count() > offset + limit,
        }
    )
