from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # v1.10 — wire the audit signal receivers exactly once.
        from core import audit_signals  # noqa: F401 — side-effect import
