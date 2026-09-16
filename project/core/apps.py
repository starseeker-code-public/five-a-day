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

        # Django logs an unhandled 500 itself, at ERROR with the traceback, and
        # that is the record which mails the admins. RequestLogMiddleware needs
        # to know that happened so it does not raise a SECOND alert for the same
        # request from a different throttle bucket — while still logging a 5xx
        # a view RETURNED, which nothing else reports at all.
        from django.core.signals import got_request_exception

        got_request_exception.connect(_mark_exception_logged, dispatch_uid="core.request_log.exception_seen")


def _mark_exception_logged(sender, request=None, **kwargs):
    if request is not None:
        request._fad_exception_logged = True
