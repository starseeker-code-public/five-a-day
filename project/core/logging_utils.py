"""Structured logging: request correlation, severity, and a JSON line format.

Three problems this module exists to solve, all of them learned from incidents
where the logs were present and still could not answer "what failed, and why?".

**1. Severity.** ``logging.StreamHandler`` writes to ``sys.stderr`` by default,
and Cloud Run labels *everything* a container writes to stderr as ERROR. So in
production every ``logger.info`` line arrived in Cloud Logging indistinguishable
from a real failure, and the console's severity filter -- the first control
anyone reaches for during an incident -- selected the whole log. A JSON line
carrying an explicit ``severity`` field is parsed by Cloud Logging and wins over
the stream it arrived on, so :class:`CloudLoggingFormatter` writes one, and the
console handler moves to stdout.

**2. Correlation.** One failure produces several records from different modules
(a send fails, the task raises, the caller catches). With nothing shared between
them they can only be joined by eyeballing timestamps, which is exactly what a
production welcome-email failure cost: three alert mails and no way to prove
they were one event. :class:`RequestContextFilter` stamps every record with the
id held in the context variable below, set per request by
``core.middleware.RequestLogMiddleware`` and per task by ``project.celery``.

**3. Where the line came from.** ``{levelname} {asctime} {module} {message}``
names neither the logger nor the line, so two modules logging the same sentence
are indistinguishable. Both formatters here carry logger name and line number,
and the JSON one fills Cloud Logging's ``sourceLocation`` so the console links
straight to the source.

Kept stdlib-only (like ``core.log_safe``): ``dictConfig`` instantiates these
while Django is still starting, long before the app registry is ready, so an
import of models or settings here would be a boot-time cycle.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from contextvars import ContextVar, Token

#: Correlation id for the request or task in scope. Empty string means "no
#: context" (a management command, a shell, an import-time log).
_request_id: ContextVar[str] = ContextVar("fad_request_id", default="")

#: Cloud Run's own trace id for this request, taken from `X-Cloud-Trace-Context`
#: and kept SEPARATE from the request id: the request id is ours and always
#: exists, while this one exists only when Google's front end put it there, and
#: inventing one would nest our entries under a request that never happened.
_trace_id: ContextVar[str] = ContextVar("fad_trace_id", default="")

#: Cloud Run's trace id is a hex string. Checked without a regex so the rule
#: reads as what it is; anything else yields "" rather than a fresh value.
_HEX = frozenset("0123456789abcdefABCDEF")

#: What we are willing to echo back into a log from a client-supplied header.
#: An inbound ``X-Request-ID`` is attacker-controlled, and a value carrying a
#: newline forges log records (CodeQL ``py/log-injection``) while one carrying a
#: megabyte floods them. Anything that does not match is REPLACED, not cleaned:
#: a partially-scrubbed id is no longer the id the client is quoting, so
#: honouring it would be worse than issuing our own.
_SAFE_ID = re.compile(r"\A[A-Za-z0-9_.:-]{1,64}\Z")

#: Record attributes ``logging`` sets itself. Anything outside this set arrived
#: through ``extra={...}`` at the call site and belongs in the JSON payload.
_RESERVED = frozenset(
    (
        "args asctime created exc_info exc_text filename funcName levelname levelno "
        "lineno message module msecs msg name pathname process processName "
        "relativeCreated stack_info taskName thread threadName request_id trace_id"
    ).split()
)


def new_request_id() -> str:
    """A fresh correlation id. Short enough to read in a terminal."""
    return uuid.uuid4().hex[:16]


def get_request_id() -> str:
    """The correlation id in scope, or an empty string outside a request/task."""
    return _request_id.get()


def set_request_id(value: str) -> Token:
    """Bind ``value`` for this context. Pass the token to :func:`reset_request_id`."""
    return _request_id.set(value)


def reset_request_id(token: Token) -> None:
    """Restore the previous id.

    Always from a ``finally``: Gunicorn reuses threads, so an id left bound
    leaks onto the next request that worker serves and quietly files one
    family's failure under another family's visit.
    """
    _request_id.reset(token)


def sanitize_request_id(value: object) -> str:
    """Return ``value`` when it is a safe correlation id, else a fresh one."""
    text = str(value or "")
    return text if _SAFE_ID.match(text) else new_request_id()


def get_trace_id() -> str:
    """Cloud Run's trace id for this request, or an empty string."""
    return _trace_id.get()


def set_trace_id(value: str) -> Token:
    """Bind ``value`` for this context. Pass the token to :func:`reset_trace_id`."""
    return _trace_id.set(value)


def reset_trace_id(token: Token) -> None:
    """Restore the previous trace id. Always from a ``finally``."""
    _trace_id.reset(token)


def sanitize_trace_id(value: object) -> str:
    """Return ``value`` when it is a plausible trace id, else an empty string.

    Unlike a request id there is no minting a replacement: this value's only job
    is to name a request Google already logged, so a made-up one would file our
    entries under somebody else's request — worse than not grouping them at all.
    """
    text = str(value or "")
    if not text or len(text) > 32 or any(char not in _HEX for char in text):
        return ""
    return text


class RequestContextFilter(logging.Filter):
    """Stamp every record with the current correlation id.

    A filter rather than a LoggerAdapter, so it also reaches records the app
    never writes itself: Django's ``django.request`` 500 entry, a warning from
    inside ``smtplib``, a security log from ``django.security``. Those are
    precisely the records that most need joining to the request that caused
    them.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Never clobber an id the call site passed in `extra=`: a task that
        # reports on work it did for a DIFFERENT context is entitled to say so,
        # and the ambient value would silently overwrite it.
        if not getattr(record, "request_id", ""):
            record.request_id = get_request_id() or "-"
        if not getattr(record, "trace_id", ""):
            record.trace_id = get_trace_id()
        return True


class HumanFormatter(logging.Formatter):
    """Readable single line for a terminal, prefixed with the id when there is one.

    The prefix is omitted outside a request so a management command does not pay
    for a column of dashes.
    """

    default_time_format = "%H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        request_id = getattr(record, "request_id", "") or ""
        prefix = f"[{request_id}] " if request_id and request_id != "-" else ""
        return f"{prefix}{base}"


class CloudLoggingFormatter(logging.Formatter):
    """One JSON object per line, in the shape Cloud Logging ingests natively.

    Fields Google reads specially: ``severity`` (so the console's severity
    filter works at all), ``message`` (the summary line),
    ``logging.googleapis.com/sourceLocation`` (links the entry to file and line)
    and ``logging.googleapis.com/trace`` (nests the entry under the Cloud Run
    *request* log, giving one expandable group per request instead of a flat
    stream). The trace field is emitted only when the project id is known --
    Google requires the fully-qualified ``projects/<id>/traces/<hex>`` form and
    silently drops a bare id.

    The rest is ours: ``logger``, ``line``, ``request_id``, plus whatever the
    call site passed as ``extra={...}``. Exception text goes into the payload
    AND is appended to ``message``, because the Logs Explorer preview row shows
    only ``message`` -- a traceback that lives solely in a collapsed field is a
    traceback nobody reads while the site is down.
    """

    #: Python level -> Cloud Logging severity. Explicit rather than
    #: ``record.levelname``, so a custom level degrades to DEFAULT instead of
    #: producing an entry Cloud Logging rejects.
    _SEVERITY = {
        logging.CRITICAL: "CRITICAL",
        logging.ERROR: "ERROR",
        logging.WARNING: "WARNING",
        logging.INFO: "INFO",
        logging.DEBUG: "DEBUG",
    }

    def __init__(self, project_id: str = "") -> None:
        super().__init__()
        self.project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT", "") or os.getenv("GCP_PROJECT", "")

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()

        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        elif record.exc_text:
            message = f"{message}\n{record.exc_text}"
        if record.stack_info:
            message = f"{message}\n{self.formatStack(record.stack_info)}"

        payload: dict[str, object] = {
            "severity": self._SEVERITY.get(record.levelno, "DEFAULT"),
            "message": message,
            "logger": record.name,
            "line": record.lineno,
            "logging.googleapis.com/sourceLocation": {
                "file": record.pathname,
                "line": str(record.lineno),
                "function": record.funcName,
            },
        }

        request_id = getattr(record, "request_id", "") or ""
        if request_id and request_id != "-":
            payload["request_id"] = request_id

        trace = getattr(record, "trace_id", "") or ""
        if trace and self.project_id:
            payload["logging.googleapis.com/trace"] = f"projects/{self.project_id}/traces/{trace}"

        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_") or key in payload:
                continue
            payload[key] = self._jsonable(value)

        # A formatter that raises loses the record AND makes the handler print
        # its own error to stderr, so an unserialisable `extra` has to degrade
        # to a readable line rather than take the log entry with it.
        try:
            return json.dumps(payload, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            return json.dumps(
                {"severity": payload["severity"], "message": message, "logger": record.name},
                ensure_ascii=False,
            )

    @staticmethod
    def _jsonable(value: object) -> object:
        if isinstance(value, str | int | float | bool | type(None)):
            return value
        return str(value)


__all__ = [
    "CloudLoggingFormatter",
    "HumanFormatter",
    "RequestContextFilter",
    "get_request_id",
    "get_trace_id",
    "new_request_id",
    "reset_request_id",
    "reset_trace_id",
    "sanitize_request_id",
    "sanitize_trace_id",
    "set_request_id",
    "set_trace_id",
]
