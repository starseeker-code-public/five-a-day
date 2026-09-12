from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # v1.10 — wire the audit signal receivers exactly once. Connected
        # per-model (by sender) rather than senderless, so untracked models keep
        # Django's fast-delete path — see audit_signals.connect().
        from core import audit_signals

        audit_signals.connect()
