from django.apps import AppConfig
from django.core.signals import got_request_exception


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # v1.10 — wire the audit signal receivers exactly once. Connected
        # per-model (by sender) rather than senderless, so untracked models keep
        # Django's fast-delete path — see audit_signals.connect().
        #
        # NOT a top-level import, and it cannot become one: `audit_signals`
        # reaches models, while this module is imported while INSTALLED_APPS is
        # still being populated. At the top of the file it would raise
        # AppRegistryNotReady at startup. `ready()` is Django's documented hook
        # for exactly this. (The sibling `got_request_exception` import below has
        # no such constraint and IS at the top.)
        from core import audit_signals

        audit_signals.connect()

        # Django logs an unhandled 500 itself, at ERROR with the traceback, and
        # that is the record which mails the admins. RequestLogMiddleware needs
        # to know that happened so it does not raise a SECOND alert for the same
        # request from a different throttle bucket — while still logging a 5xx
        # a view RETURNED, which nothing else reports at all.
        got_request_exception.connect(_mark_exception_logged, dispatch_uid="core.request_log.exception_seen")


def _mark_exception_logged(sender, request=None, **kwargs):
    if request is not None:
        request._fad_exception_logged = True
