"""Group-capacity notifications (v1.29.5).

One function, `notify_capacity_freed`, called from the `post_save` signal on
`students.Student` when a student is deactivated.

**Why it is here and not in `core/views/waiting_list.py`, where it used to
live.** Nothing in that module called it — it was defined and exported there,
and its only caller was the model signal, so `students/models.py` reached UP
into a view module to fire it. That is the wrong direction (the dependency flow
in CLAUDE.md is `students ← core`, not the reverse), it forced the signal to use
a function-body import to dodge an app-loading cycle, and it meant importing the
whole view module — messages, decorators, forms — to log one history row.

This module imports `core.models` and nothing else, so it stays a leaf.
"""

from __future__ import annotations

from core.models import HistoryLog


def notify_capacity_freed(student) -> None:
    """Log a HistoryLog entry when a deactivated student's group has waiters.

    Idempotent in the sense that matters: the caller is responsible for invoking
    it only on the `active` True → False transition, so it does not try to work
    out for itself whether the spot is newly free.
    """
    if not student.group_id:
        return
    group = student.group
    # Refresh counts — the student we just deactivated should already be excluded
    # by `active=True` in `enrolled_count`.
    waiting_candidates = group.students.filter(active=True, is_waiting=True).count()
    if not waiting_candidates:
        return

    HistoryLog.log(
        "waiting_list_spot_open",
        (
            f"Hueco disponible en {group.group_name} — "
            f"{waiting_candidates} estudiante{'s' if waiting_candidates != 1 else ''} en lista de espera."
        ),
        icon="notifications_active",
    )


__all__ = ["notify_capacity_freed"]
