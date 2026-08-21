"""CR/LF-stripping helper for anything user-controlled that reaches a log.

Log injection (CodeQL ``py/log-injection``): a value containing a carriage
return or newline lets an attacker forge extra log lines -- a fake "login
succeeded" record, or a spoofed traceback that sends an on-call engineer down
the wrong path. The remediation is to strip the line terminators before the
value is formatted into the record, which is what :func:`safe_log` does. It
also caps the length so a multi-kilobyte field can't flood the log, and drops
ESC so a tailed log file can't be fed terminal escape sequences.

Kept as a stdlib-only leaf module (no Django imports, no models) so any app can
import it without creating a dependency cycle.
"""

from __future__ import annotations

_MAX_LEN = 200

# Only the characters a log writer emits verbatim can forge a record, so this
# is deliberately the short list rather than everything ``str.splitlines()``
# treats as a boundary.
_LINE_BREAKS = ("\r\n", "\r", "\n", "\v", "\f", "\x1b")


def safe_log(value: object, max_len: int = _MAX_LEN) -> str:
    """Return ``value`` as a single-line, length-capped string safe to log."""
    text = str(value)
    for char in _LINE_BREAKS:
        text = text.replace(char, " ")
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text


__all__ = ["safe_log"]
