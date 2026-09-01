"""
Desarrollos (features) views — the Jira-style epic board that sits beside the QA
backlog.

A ``Feature`` describes a piece of work to build; the backlog tasks broken out
of it are the individual tickets. Everything here is QA-only
(``@qa_access_required``), same as ``testing_tools``.
"""

import json
import logging
from datetime import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from core.decorators import qa_access_required
from core.models import BacklogTask, Feature
from core.views.testing_tools import VALID_PRIORITIES, email_backlog_task_created

logger = logging.getLogger(__name__)

VALID_FEATURE_STATUSES = {"open", "in_progress", "done"}

# Prefilled into the "nuevo desarrollo" description box so every development is
# written up the same way. Kept server-side (rather than hard-coded in the
# template's JS) so the board and the detail page cannot drift apart.
FEATURE_DESCRIPTION_TEMPLATE = """h2. Resumen
_Una frase: que se quiere construir._

h2. Contexto / Problema
_Que ocurre hoy, a quien afecta y por que merece la pena resolverlo._

h2. Objetivo
_Como se ve el mundo cuando esto este hecho._

h2. Alcance
*
*

h2. Fuera de alcance
*

h2. Criterios de aceptacion
* [ ]
* [ ]

h2. Notas tecnicas
_Modelos, vistas, plantillas o servicios afectados._
"""


def _features_qs():
    """Developments with the OPEN ones first, newest first within each half.

    Same shape as ``_backlog_tasks_qs``: ``Q()`` annotates a boolean and False
    sorts before True, so finished developments fall to the bottom instead of
    holding their slot by creation date. The task counters are annotated so the
    board does not fire two extra queries per row.
    """
    return Feature.objects.annotate(
        is_done=Q(status="done"),
        n_tasks=Count("tasks", distinct=True),
        n_done_tasks=Count("tasks", filter=Q(tasks__status="done"), distinct=True),
    ).order_by("is_done", "-created_at")


@qa_access_required
def features_view(request):
    """Render the Desarrollos board."""
    context = {
        "features": _features_qs()[:50],
        "description_template": FEATURE_DESCRIPTION_TEMPLATE,
        "app_version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
    }
    return render(request, "features.html", context)


@qa_access_required
def feature_detail_view(request, feature_id):
    """Render a single development with the backlog tasks broken out of it."""
    feature = get_object_or_404(Feature, pk=feature_id)
    tasks = feature.tasks.annotate(is_done=Q(status="done")).order_by("is_done", "-created_at")
    context = {
        "feature": feature,
        "tasks": tasks,
        "priority_choices": BacklogTask.PRIORITY_CHOICES,
        "status_choices": Feature.STATUS_CHOICES,
    }
    return render(request, "feature_detail.html", context)


def _feature_payload(feature):
    """The JSON shape the board's JS renders a row from."""
    return {
        "id": feature.id,
        "title": feature.title,
        "description": feature.description,
        "status": feature.status,
        "status_display": feature.get_status_display(),
        "deadline": feature.deadline.isoformat() if feature.deadline else None,
        "deadline_display": feature.deadline.strftime("%d/%m/%Y") if feature.deadline else None,
        "is_overdue": feature.is_overdue,
        "created_by": feature.created_by,
        "created_at": feature.created_at.strftime("%d/%m/%Y %H:%M"),
        "task_count": feature.task_count,
        "done_task_count": feature.done_task_count,
    }


def _email_feature(feature, kind):
    """Notify support (``created``) or the admin teachers (``done``) about a
    development — the same two moments the backlog notifies on, so an epic is as
    visible as a ticket. Never raises: a mail failure must not undo the write.
    """
    if kind == "created":
        recipients = [getattr(settings, "SUPPORT_EMAIL", None)]
        subject = f"[DESARROLLO] {feature.title}"
        headline = "Nuevo desarrollo registrado en el panel de QA"
    else:
        from students.models import Teacher

        recipients = list(Teacher.objects.filter(admin=True, active=True).values_list("email", flat=True))
        subject = f"[DESARROLLO][HECHO] {feature.title}"
        headline = "Un desarrollo se ha marcado como HECHO"

    recipients = [e for e in recipients if e]
    if not recipients:
        return

    deadline = feature.deadline.strftime("%d/%m/%Y") if feature.deadline else "(sin fecha limite)"
    body = (
        f"{headline}\n"
        f"{'=' * 50}\n\n"
        f"Titulo:        {feature.title}\n"
        f"Estado:        {feature.get_status_display()}\n"
        f"Fecha limite:  {deadline}\n"
        f"Creado por:    {feature.created_by}\n"
        f"Fecha:         {feature.created_at:%Y-%m-%d %H:%M}\n"
        f"Tareas:        {feature.done_task_count}/{feature.task_count} hechas\n\n"
        f"Descripcion:\n{feature.description or '(ninguna)'}\n\n"
        f"{'=' * 50}\n"
        f"Five a Day — Entorno QA\n"
    )
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=True,
        )
    except Exception:  # noqa: BLE001 — never block the write on email
        logger.exception("Error sending the '%s' development notification", kind)


def _parse_deadline(raw):
    """Return ``(date_or_None, ok)`` for a deadline coming off the wire.

    Blank / null means "no deadline", which is the field's default and a
    legitimate value — only a non-empty string that is not a date is an error.
    """
    if raw in (None, ""):
        return None, True
    parsed = parse_date(str(raw).strip())
    return parsed, parsed is not None


@qa_access_required
@require_http_methods(["POST"])
def api_create_feature(request):
    """Create a development and email it to support.

    JSON only — a development carries no screenshot, so unlike
    ``api_create_backlog_task`` there is no multipart branch.
    """
    try:
        data = json.loads(request.body)
        title = (data.get("title") or "").strip()
        description = (data.get("description") or "").strip()

        if not title:
            return JsonResponse({"success": False, "message": "El titulo es obligatorio."}, status=400)

        deadline, ok = _parse_deadline(data.get("deadline"))
        if not ok:
            return JsonResponse({"success": False, "message": "La fecha limite no es valida."}, status=400)

        feature = Feature(
            title=title,
            description=description,
            deadline=deadline,
            created_by=request.session.get("username", "anonymous"),
        )
        # `.create()` / `.save()` do not validate — status choices and field
        # lengths only hold if `full_clean()` is called explicitly.
        feature.full_clean()
        feature.save()

        _email_feature(feature, "created")
        return JsonResponse({"success": True, "feature": _feature_payload(feature)})
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "JSON invalido."}, status=400)
    except ValidationError as exc:
        return JsonResponse({"success": False, "message": " ".join(exc.messages)}, status=400)
    except Exception:
        logger.exception("Error creating a development")
        return JsonResponse(
            {"success": False, "message": "No se pudo crear el desarrollo. Revisa los datos."},
            status=500,
        )


@qa_access_required
@require_http_methods(["POST"])
def api_update_feature(request, feature_id):
    """Update a development's title, description, status and/or deadline.

    Every field is optional in the payload so the board can flip a status and
    the detail page can save a description without either clobbering the other.
    Sending ``"deadline": null`` clears the date (back to the default).
    """
    try:
        data = json.loads(request.body)
        feature = Feature.objects.get(pk=feature_id)
        was_done = feature.status == "done"
        updated = []

        if "status" in data:
            new_status = data.get("status")
            if new_status not in VALID_FEATURE_STATUSES:
                return JsonResponse({"success": False, "message": "Estado no valido."}, status=400)
            feature.status = new_status
            updated.append("status")

        if "deadline" in data:
            deadline, ok = _parse_deadline(data.get("deadline"))
            if not ok:
                return JsonResponse({"success": False, "message": "La fecha limite no es valida."}, status=400)
            feature.deadline = deadline
            updated.append("deadline")

        if "title" in data:
            title = (data.get("title") or "").strip()
            if not title:
                return JsonResponse({"success": False, "message": "El titulo es obligatorio."}, status=400)
            feature.title = title
            updated.append("title")

        if "description" in data:
            feature.description = (data.get("description") or "").strip()
            updated.append("description")

        if not updated:
            return JsonResponse({"success": False, "message": "Nada que actualizar."}, status=400)

        feature.full_clean()
        feature.save()

        if feature.status == "done" and not was_done:
            _email_feature(feature, "done")

        return JsonResponse({"success": True, "feature": _feature_payload(feature)})
    except Feature.DoesNotExist:
        return JsonResponse({"success": False, "message": "Desarrollo no encontrado."}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "JSON invalido."}, status=400)
    except ValidationError as exc:
        return JsonResponse({"success": False, "message": " ".join(exc.messages)}, status=400)
    except Exception:
        logger.exception("Error updating development %d", int(feature_id))
        return JsonResponse(
            {"success": False, "message": "No se pudo actualizar el desarrollo."},
            status=500,
        )


@qa_access_required
@require_http_methods(["POST"])
def api_create_feature_task(request, feature_id):
    """Break a task out of a development, straight into the QA backlog.

    The row created is an ordinary ``BacklogTask`` — it appears on ``/testing/``
    like any other and carries a priority — with ``feature`` set so the epic can
    count it. No screenshot: a task spawned from an epic describes work to do,
    not a defect somebody saw on screen.
    """
    try:
        data = json.loads(request.body)
        feature = Feature.objects.get(pk=feature_id)
        title = (data.get("title") or "").strip()
        description = (data.get("description") or "").strip()
        priority = data.get("priority", "medium")

        if not title:
            return JsonResponse({"success": False, "message": "El titulo es obligatorio."}, status=400)
        if priority not in VALID_PRIORITIES:
            return JsonResponse({"success": False, "message": "Prioridad no valida."}, status=400)

        task = BacklogTask.objects.create(
            title=title,
            description=description,
            priority=priority,
            created_by=request.session.get("username", "anonymous"),
            feature=feature,
        )
        email_backlog_task_created(task, context_line=f"Desarrollo:  {feature.title}")

        return JsonResponse(
            {
                "success": True,
                "task": {
                    "id": task.id,
                    "title": task.title,
                    "description": task.description,
                    "priority": task.priority,
                    "priority_display": task.get_priority_display(),
                    "status": task.status,
                    "status_display": task.get_status_display(),
                    "created_by": task.created_by,
                    "created_at": task.created_at.strftime("%d/%m/%Y %H:%M"),
                },
                "task_count": feature.task_count,
                "done_task_count": feature.done_task_count,
            }
        )
    except Feature.DoesNotExist:
        return JsonResponse({"success": False, "message": "Desarrollo no encontrado."}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "JSON invalido."}, status=400)
    except Exception:
        logger.exception("Error creating a task for development %d", int(feature_id))
        return JsonResponse(
            {"success": False, "message": "No se pudo crear la tarea."},
            status=500,
        )


@qa_access_required
@require_http_methods(["GET"])
def export_features(request):
    """Download the developments as JSON or CSV.

    ``?format=csv`` (default ``json``) and ``?scope=active|all`` (default
    ``active``, i.e. everything not yet done) so the export matches the board,
    exactly like ``export_backlog_tasks``.
    """
    export_format = (request.GET.get("format") or "json").lower()
    scope = (request.GET.get("scope") or "active").lower()

    features = _features_qs()
    if scope != "all":
        features = features.exclude(status="done")

    rows = [
        {
            "id": f.id,
            "title": f.title,
            "description": f.description,
            "status": f.status,
            "status_display": f.get_status_display(),
            "deadline": f.deadline.isoformat() if f.deadline else "",
            "created_by": f.created_by,
            "created_at": f.created_at.isoformat(),
            "updated_at": f.updated_at.isoformat(),
            "task_count": f.n_tasks,
            "done_task_count": f.n_done_tasks,
        }
        for f in features
    ]

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    filename = f"desarrollos-{scope}-{stamp}"

    if export_format == "csv":
        import csv
        import io

        buffer = io.StringIO()
        fieldnames = list(rows[0].keys()) if rows else ["id", "title", "description", "status", "deadline"]
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        # BOM so Excel opens the accented Spanish text correctly.
        response = HttpResponse("﻿" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
        return response

    payload = {
        "exported_at": datetime.now().isoformat(),
        "app_version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "scope": scope,
        "count": len(rows),
        "features": rows,
    }
    response = HttpResponse(
        json.dumps(payload, indent=2, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}.json"'
    return response
