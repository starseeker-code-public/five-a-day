import logging

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


def handler400(request, exception=None):
    return render(request, "400.html", status=400)


def handler403(request, exception=None):
    return render(request, "403.html", status=403)


def handler404(request, exception=None):
    return render(request, "404.html", status=404)


def handler405(request, exception=None):
    return render(request, "405.html", status=405)


def handler500(request):
    return render(request, "500.html", status=500)


def test_error_400(request):
    return render(request, "400.html", status=400)


def test_error_403(request):
    return render(request, "403.html", status=403)


def test_error_404(request):
    return render(request, "404.html", status=404)


def test_error_405(request):
    return render(request, "405.html", status=405)


def test_error_500(request):
    return render(request, "500.html", status=500)


def _database_probe(request):
    """Inspect the database this process is actually talking to.

    Returns (payload, ok). Kept out of the default /health/ response because
    liveness probes must not depend on the database being up — see health_check.
    """
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    probe = {"connected": False}

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        probe["connected"] = True

        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        probe["unapplied_migrations"] = len(executor.migration_plan(targets))
        probe["applied_migrations"] = len(executor.loader.applied_migrations)
    except Exception:
        # Never echo the exception text — it leaks host, database and user.
        logger.exception("Health deep probe: database check failed")
        return probe, False

    # Row counts identify WHICH database this is, not merely that one answered.
    # A release can deploy the right code against a valid but wrong database and
    # look perfectly healthy, so this is the only check that catches it. Gated on
    # a shared secret because /health/ is public.
    token = getattr(settings, "HEALTH_PROBE_TOKEN", "")
    supplied = request.headers.get("X-Probe-Token", "")
    if token and constant_time_compare(supplied, token):
        from billing.models import Enrollment, Payment
        from students.models import Parent, Student

        probe["name"] = connection.settings_dict.get("NAME")
        probe["counts"] = {
            "students": Student.objects.count(),
            "parents": Parent.objects.count(),
            "enrollments": Enrollment.objects.count(),
            "payments": Payment.objects.count(),
        }

    return probe, True


@csrf_exempt
def health_check(request):
    """Liveness probe, plus an opt-in deep probe for deploy verification.

    Default (`/health/`) is deliberately shallow: it reports code and config only
    and never touches the database, so an uptime check cannot be taken down by a
    transient database blip.

    `/health/?deep=1` additionally reports database connectivity and migration
    state, and returns 503 when the database cannot be reached. Send a valid
    X-Probe-Token to also receive row counts. A shallow 200 proves the right
    IMAGE is running; only the deep probe can show which DATABASE it is using.
    """
    payload = {
        "status": "healthy",
        "service": "fiveaday",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
    }

    if request.GET.get("deep") != "1":
        return JsonResponse(payload, status=200)

    probe, ok = _database_probe(request)
    payload["database"] = probe
    if not ok:
        payload["status"] = "degraded"
        return JsonResponse(payload, status=503)

    return JsonResponse(payload, status=200)
