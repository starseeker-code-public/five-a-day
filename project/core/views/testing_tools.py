"""
Testing Tools views — QA dashboard with project info, seeding, backlog, and
error-reporting toggle.
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime

import django
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.text import get_valid_filename
from django.views.decorators.http import require_http_methods

from core.decorators import qa_access_required
from core.models import BacklogTask, QAConfiguration
from core.utils import csv_safe

logger = logging.getLogger(__name__)

VALID_PRIORITIES = {"low", "medium", "high"}


def _backlog_tasks_qs():
    """Backlog ordered with the OPEN work first, newest first within each half.

    The model's Meta ordering is `-created_at` alone, so a task marked done stayed
    wherever its creation date put it and pushed live tickets down the list — and,
    with the dashboard capped at 50, off the page entirely. `Q()` annotates a
    boolean, and False sorts before True, so done tasks fall to the bottom.
    """
    # `select_related("feature")`: the dashboard renders `task.feature.title` for any
    # task broken out of a development, which was one query per row (51 for 50).
    return (
        BacklogTask.objects.select_related("feature")
        .annotate(is_done=Q(status="done"))
        .order_by("is_done", "-created_at")
    )


# Leading bytes of the formats a screenshot can plausibly be. Checked in
# addition to the client-declared content type, which is not evidence.
_IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",  # JPEG
    b"GIF87a",  # GIF
    b"GIF89a",
    b"BM",  # BMP
)


def _looks_like_image(upload) -> bool:
    """True if `upload` starts with the magic bytes of a known image format.

    Reads the first 32 bytes and rewinds, so the caller can still attach the
    file afterwards. WEBP and AVIF are RIFF/ISO-BMFF containers, hence the
    substring checks rather than a prefix.
    """
    try:
        upload.seek(0)
        head = upload.read(32)
        upload.seek(0)
    except Exception:
        return False

    if head.startswith(_IMAGE_MAGIC):
        return True
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    return head[4:8] == b"ftyp" and b"avif" in head[8:24]


def _git_info():
    """Return branch + last commit info. Single subprocess call, never raises."""
    fmt = "%H%n%h%n%s%n%an%n%ci"
    try:
        # `-c safe.directory=*` avoids git's "dubious ownership" refusal when
        # the repo is owned by a different user than the process (common with
        # bind mounts / clones done as root). Requires git in the image.
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "log", "-1", f"--pretty=format:{fmt}"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=settings.BASE_DIR.parent,
        )
        if result.returncode != 0:
            return {}
        lines = result.stdout.strip().split("\n")
        branch = subprocess.run(
            ["git", "-c", "safe.directory=*", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=settings.BASE_DIR.parent,
        ).stdout.strip()
        return {
            "branch": branch or "—",
            "commit_id_full": lines[0] if len(lines) > 0 else "—",
            "commit_id": lines[1] if len(lines) > 1 else "—",
            "commit_message": lines[2] if len(lines) > 2 else "—",
            "commit_author": lines[3] if len(lines) > 3 else "—",
            "commit_date": lines[4] if len(lines) > 4 else "—",
        }
    except Exception:
        return {}


@qa_access_required
def testing_tools_view(request):
    """Render the QA testing tools page."""
    git = _git_info()
    qa_config = QAConfiguration.get_config()
    tasks = _backlog_tasks_qs()[:50]

    context = {
        "git": git,
        "qa_config": qa_config,
        "tasks": tasks,
        "app_version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "debug": settings.DEBUG,
        "database_engine": settings.DATABASES["default"]["ENGINE"],
        "database_name": settings.DATABASES["default"].get("NAME", "—"),
        "python_version": sys.version.split()[0],
        "django_version": django.get_version(),
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": settings.TIME_ZONE,
    }
    return render(request, "testing_tools.html", context)


@qa_access_required
@require_http_methods(["POST"])
def api_seed_database(request):
    """Run the seed_testdata management command via AJAX."""
    from io import StringIO

    from django.core.management import call_command

    try:
        data = json.loads(request.body)
        reset = data.get("reset", False)

        out = StringIO()
        args = ["seed_testdata"]
        kwargs = {"stdout": out}
        if reset:
            kwargs["reset"] = True

        call_command(*args, **kwargs)
        output = out.getvalue()
        return JsonResponse({"success": True, "message": output})
    except Exception:
        logger.exception("seed_testdata command failed")
        return JsonResponse(
            {"success": False, "message": "El comando de seed ha fallado. Revisa los logs."},
            status=500,
        )


def email_backlog_task_created(task, screenshot=None, context_line=""):
    """Email SUPPORT_EMAIL about a newly created backlog task.

    Shared with the Desarrollos board, which breaks tasks out of an epic and
    must announce them exactly like a task typed straight into ``/testing/`` —
    hence ``context_line``, an extra header line naming the development the task
    came from.

    ``screenshot`` is an in-memory upload attached to the message and NEVER
    persisted; tasks spawned from a development never carry one. Never raises.
    """
    support_email = getattr(settings, "SUPPORT_EMAIL", None)
    if not support_email:
        return

    from django.core.mail import EmailMessage

    body = (
        f"Nueva tarea en el backlog de QA\n"
        f"{'=' * 50}\n\n"
        f"Titulo:      {task.title}\n"
        f"Prioridad:   {task.priority}\n"
        f"Creado por:  {task.created_by}\n"
        f"Fecha:       {task.created_at:%Y-%m-%d %H:%M}\n"
        f"{context_line}\n\n"
        f"Descripcion:\n{task.description or '(ninguna)'}\n\n"
        f"{'Se adjunta una captura de pantalla.' if screenshot else ''}\n"
        f"{'=' * 50}\n"
        f"Five a Day — Entorno QA\n"
    )
    try:
        email = EmailMessage(
            subject=f"[BACKLOG][{task.priority.upper()}] {task.title}",
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[support_email],
        )
        if screenshot is not None:
            # `get_valid_filename` on the basename: the client controls
            # `screenshot.name`, and it reaches a MIME header here.
            safe_name = get_valid_filename(os.path.basename(screenshot.name or "captura.png")) or "captura.png"
            screenshot.seek(0)
            email.attach(safe_name, screenshot.read(), screenshot.content_type)
        email.send(fail_silently=True)
    except Exception:  # noqa: BLE001 — never block task creation on email failure
        logger.exception("Error sending the backlog-task notification")


@qa_access_required
@require_http_methods(["POST"])
def api_create_backlog_task(request):
    """Create a backlog task and email it to support.

    An optional screenshot is sent multipart and ATTACHED to the email only —
    it is never stored on disk or in the DB (deliberate, to avoid image storage
    spiralling out of control). Max 5 MB, images only.
    """
    try:
        # Multipart (so a screenshot can ride along); fall back to JSON.
        if request.content_type and request.content_type.startswith("multipart/"):
            title = (request.POST.get("title") or "").strip()
            description = (request.POST.get("description") or "").strip()
            priority = request.POST.get("priority", "medium")
            screenshot = request.FILES.get("screenshot")
        else:
            data = json.loads(request.body)
            title = data.get("title", "").strip()
            description = data.get("description", "").strip()
            priority = data.get("priority", "medium")
            screenshot = None

        if not title:
            return JsonResponse({"success": False, "message": "El titulo es obligatorio."}, status=400)
        if priority not in VALID_PRIORITIES:
            return JsonResponse({"success": False, "message": "Prioridad no valida."}, status=400)
        if screenshot is not None:
            if screenshot.size > 5 * 1024 * 1024:
                return JsonResponse({"success": False, "message": "La imagen supera los 5 MB."}, status=400)
            # `content_type` is whatever the CLIENT declared — a browser sends it
            # from the file extension and a scripted POST can claim anything. So
            # check the declared type AND the actual leading bytes, otherwise
            # this endpoint forwards an arbitrary file to the support inbox
            # under an image's name.
            if not (screenshot.content_type or "").startswith("image/"):
                return JsonResponse({"success": False, "message": "El adjunto debe ser una imagen."}, status=400)
            if not _looks_like_image(screenshot):
                return JsonResponse(
                    {"success": False, "message": "El adjunto no parece una imagen valida."}, status=400
                )

        username = request.session.get("username", "anonymous")
        task = BacklogTask.objects.create(
            title=title,
            description=description,
            priority=priority,
            created_by=username,
        )

        # Email support — the screenshot is attached in-memory (never persisted).
        email_backlog_task_created(task, screenshot=screenshot)

        return JsonResponse(
            {
                "success": True,
                "task": {
                    "id": task.id,
                    "title": task.title,
                    "priority": task.priority,
                    "status": task.status,
                    "created_by": task.created_by,
                    "created_at": task.created_at.strftime("%d/%m/%Y %H:%M"),
                },
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "JSON invalido."}, status=400)
    except Exception:
        logger.exception("Error creating backlog task")
        return JsonResponse(
            {"success": False, "message": "No se pudo crear la tarea. Revisa los datos."},
            status=500,
        )


@qa_access_required
@require_http_methods(["GET"])
def export_backlog_tasks(request):
    """Download the backlog as JSON or CSV.

    `?format=csv` (default `json`) and `?scope=active|all` (default `active`,
    meaning everything not yet done) so the export matches what the dashboard
    is showing. Every field on the task is included.
    """
    export_format = (request.GET.get("format") or "json").lower()
    scope = (request.GET.get("scope") or "active").lower()

    tasks = _backlog_tasks_qs()
    if scope != "all":
        tasks = tasks.exclude(status="done")

    rows = [
        {
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "priority": t.priority,
            "priority_display": t.get_priority_display(),
            "status": t.status,
            "status_display": t.get_status_display(),
            "created_by": t.created_by,
            "created_at": t.created_at.isoformat(),
            "updated_at": t.updated_at.isoformat(),
        }
        for t in tasks
    ]

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    filename = f"backlog-{scope}-{stamp}"

    if export_format == "csv":
        import csv
        import io

        buffer = io.StringIO()
        fieldnames = list(rows[0].keys()) if rows else ["id", "title", "description", "priority", "status"]
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        # Titles and descriptions are free text; a leading =/+/-/@ would be
        # evaluated as a formula by whoever opens the export.
        writer.writerows([{k: csv_safe(v) for k, v in row.items()} for row in rows])
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
        "tasks": rows,
    }
    response = HttpResponse(
        json.dumps(payload, indent=2, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}.json"'
    return response


@qa_access_required
@require_http_methods(["POST"])
def api_update_backlog_task(request, task_id):
    """Update a backlog task status."""
    try:
        data = json.loads(request.body)

        # A payload carrying only `verified` toggles the QA tick and nothing else.
        # It is the tester's own mark ("I checked this and it is correct") and must
        # not touch `status` or send the developer notification that `done` does.
        if "status" not in data and "verified" in data:
            task = BacklogTask.objects.get(pk=task_id)
            task.verified = bool(data.get("verified"))
            task.save(update_fields=["verified", "updated_at"])
            return JsonResponse({"success": True, "verified": task.verified})

        new_status = data.get("status")
        if new_status not in ("open", "in_progress", "done"):
            return JsonResponse({"success": False, "message": "Estado no valido."}, status=400)

        task = BacklogTask.objects.get(pk=task_id)
        was_done = task.status == "done"
        task.status = new_status
        task.save()

        # When a task is completed, notify the (seeded) admin teachers by email.
        if new_status == "done" and not was_done:
            _email_task_done(task)

        return JsonResponse({"success": True})
    except BacklogTask.DoesNotExist:
        return JsonResponse({"success": False, "message": "Tarea no encontrada."}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "JSON invalido."}, status=400)
    except Exception:
        logger.exception("Error updating backlog task %d", int(task_id))
        return JsonResponse(
            {"success": False, "message": "No se pudo actualizar la tarea."},
            status=500,
        )


def _email_task_done(task):
    """Email the admin teachers that a backlog task was completed (testing env)."""
    from students.models import Teacher

    recipients = list(Teacher.objects.filter(admin=True, active=True).values_list("email", flat=True))
    recipients = [e for e in recipients if e]
    if not recipients:
        return
    try:
        send_mail(
            subject=f"[BACKLOG][HECHO] {task.title}",
            message=(
                f"Una tarea del backlog de QA se ha marcado como HECHA.\n"
                f"{'=' * 50}\n\n"
                f"Titulo:      {task.title}\n"
                f"Prioridad:   {task.priority}\n"
                f"Creada por:  {task.created_by}\n"
                f"Fecha:       {task.created_at:%Y-%m-%d %H:%M}\n\n"
                f"Descripcion:\n{task.description or '(ninguna)'}\n\n"
                f"{'=' * 50}\nFive a Day — Entorno QA\n"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=True,
        )
    except Exception:  # noqa: BLE001 — never block the status update on email
        pass


@qa_access_required
@require_http_methods(["POST"])
def api_toggle_error_email(request):
    """Toggle the QA error email reporting on/off."""
    try:
        data = json.loads(request.body)
        enabled = data.get("enabled", False)
        config = QAConfiguration.get_config()
        config.error_email_enabled = bool(enabled)
        config.save()
        return JsonResponse({"success": True, "enabled": config.error_email_enabled})
    except Exception:
        logger.exception("Error toggling QA error-email setting")
        return JsonResponse(
            {"success": False, "message": "No se pudo guardar la preferencia."},
            status=500,
        )


@qa_access_required
@require_http_methods(["POST"])
def api_mark_ready(request):
    """Email SUPPORT_EMAIL that an admin marked this version as ready to ship,
    with a full snapshot of the version / environment / last-commit info.

    Since the QA sign-off gate this ALSO sets QAConfiguration.ready_for_prod,
    which /health/?deep=1 exposes and deploy-production.yml's preflight requires
    before a release can be armed for approval. The flag is set only after the
    email goes out, so "success" always means both happened; the nightly testing
    deploy resets it to False for every new version (set_ready_for_prod off)."""
    support_email = getattr(settings, "SUPPORT_EMAIL", None)
    if not support_email:
        return JsonResponse({"success": False, "message": "SUPPORT_EMAIL no está configurado."}, status=500)

    user = getattr(request, "user", None)
    user_email = getattr(user, "email", "") or request.session.get("username", "desconocido")
    git = _git_info()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    subject = f"[READY TO SHIP] v{settings.APP_VERSION} — {user_email}"
    body = (
        f"{user_email} ha marcado esta versión como LISTA PARA DESPLEGAR.\n"
        f"{'=' * 55}\n\n"
        f"Aplicación\n"
        f"  Versión:        v{settings.APP_VERSION}\n"
        f"  Entorno:        {settings.ENVIRONMENT}\n"
        f"  Debug:          {settings.DEBUG}\n"
        f"  Python:         {sys.version.split()[0]}\n"
        f"  Django:         {django.get_version()}\n"
        f"  Base de datos:  {settings.DATABASES['default'].get('NAME', '—')}\n"
        f"  Motor BD:       {settings.DATABASES['default']['ENGINE']}\n"
        f"  Zona horaria:   {settings.TIME_ZONE}\n"
        f"  Fecha/hora:     {now}\n\n"
        f"Último commit\n"
        f"  Rama:     {git.get('branch', '—')}\n"
        f"  Commit:   {git.get('commit_id_full', '—')}\n"
        f"  Mensaje:  {git.get('commit_message', '—')}\n"
        f"  Autor:    {git.get('commit_author', '—')}\n"
        f"  Fecha:    {git.get('commit_date', '—')}\n\n"
        f"{'=' * 55}\n"
        f"Marcado por: {user_email}\n"
        f"Five a Day — Entorno QA\n"
    )
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[support_email],
            fail_silently=False,
        )
    except Exception:  # noqa: BLE001 — surface a send failure to the UI, details to the log
        logger.exception("Error sending the 'ready to ship' notification")
        return JsonResponse(
            {"success": False, "message": "Error al enviar el aviso. Revisa los logs."},
            status=500,
        )

    config = QAConfiguration.get_config()
    config.ready_for_prod = True
    config.save()

    return JsonResponse(
        {
            "success": True,
            "ready_for_prod": True,
            "message": f"Enviado a {support_email}. Versión desbloqueada para producción.",
        }
    )
