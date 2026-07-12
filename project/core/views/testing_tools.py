"""
Testing Tools views — QA dashboard with project info, seeding, backlog, and
error-reporting toggle.
"""

import json
import subprocess
import sys
from datetime import datetime

import django
from django.conf import settings
from django.core.mail import send_mail
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from core.decorators import qa_access_required
from core.models import BacklogTask, QAConfiguration

VALID_PRIORITIES = {"low", "medium", "high"}


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
    tasks = BacklogTask.objects.all()[:50]

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
    except Exception as e:
        return JsonResponse({"success": False, "message": str(e)}, status=500)


@qa_access_required
@require_http_methods(["POST"])
def api_create_backlog_task(request):
    """Create a backlog task and optionally email it to support."""
    try:
        data = json.loads(request.body)
        title = data.get("title", "").strip()
        description = data.get("description", "").strip()
        priority = data.get("priority", "medium")

        if not title:
            return JsonResponse({"success": False, "message": "El titulo es obligatorio."}, status=400)
        if priority not in VALID_PRIORITIES:
            return JsonResponse({"success": False, "message": "Prioridad no valida."}, status=400)

        username = request.session.get("username", "anonymous")
        task = BacklogTask.objects.create(
            title=title,
            description=description,
            priority=priority,
            created_by=username,
        )

        # Send email to support
        support_email = getattr(settings, "SUPPORT_EMAIL", None)
        if support_email:
            subject = f"[BACKLOG][{priority.upper()}] {title}"
            body = (
                f"Nueva tarea en el backlog de QA\n"
                f"{'=' * 50}\n\n"
                f"Titulo:      {title}\n"
                f"Prioridad:   {priority}\n"
                f"Creado por:  {username}\n"
                f"Fecha:       {task.created_at:%Y-%m-%d %H:%M}\n\n"
                f"Descripcion:\n{description or '(ninguna)'}\n\n"
                f"{'=' * 50}\n"
                f"Five a Day — Entorno QA\n"
            )
            try:
                send_mail(
                    subject=subject,
                    message=body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[support_email],
                    fail_silently=True,
                )
            except Exception:
                pass  # Never block task creation on email failure

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


@qa_access_required
@require_http_methods(["POST"])
def api_update_backlog_task(request, task_id):
    """Update a backlog task status."""
    try:
        data = json.loads(request.body)
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
    except Exception as e:
        return JsonResponse({"success": False, "message": str(e)}, status=500)


@qa_access_required
@require_http_methods(["POST"])
def api_mark_ready(request):
    """Email SUPPORT_EMAIL that an admin marked this version as ready to ship,
    with a full snapshot of the version / environment / last-commit info."""
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
    except Exception as e:  # noqa: BLE001 — surface the send failure to the UI
        return JsonResponse({"success": False, "message": f"Error al enviar: {e}"}, status=500)

    return JsonResponse({"success": True, "message": f"Enviado a {support_email}"})
