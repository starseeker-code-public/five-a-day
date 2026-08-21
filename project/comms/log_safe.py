"""CR/LF-stripping helper for anything user-controlled that reaches a log.

Deliberately a near-copy of ``core.log_safe``: ``comms`` must not import from
``core`` (see the dependency flow in CLAUDE.md), and duplicating ~10 lines is
cheaper than inverting that. Stdlib only, no Django, no models.

See the `safe_log` docstring in ``core/log_safe.py`` for the full rationale.
Short version: a value containing a carriage return or newline lets an attacker
forge extra log lines, so line terminators are stripped and the value is
length-capped before it is formatted into a record.

Note that this makes the code safe but does NOT clear CodeQL's
``py/log-injection``, which treats ``str.replace`` as taint-preserving. Where a
value can be coerced (an int id) or simply left out of the record, prefer that.
"""

from __future__ import annotations

_MAX_LEN = 200

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
