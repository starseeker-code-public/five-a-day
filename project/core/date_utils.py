"""Small date helpers shared across core views — a LEAF module (stdlib only).

`first_day_of_next_month` lived in `core.views.students`, and
`core.views.waiting_list` imported it from there. But `students` imports
`waiting_list` (the waiting-list redirect flow), so that back-import created a
`waiting_list → students → waiting_list` cycle CodeQL flagged. A pure date
helper has no business forcing that edge; it lives here instead, and both
modules import it from a leaf that imports nothing of ours.
"""

from datetime import date


def first_day_of_next_month(today=None):
    """The 1st of the month after ``today`` (defaults to today).

    Used as the ``cancel_from`` boundary when a student stops being billed: the
    month currently being taught stays owed, everything from next month on is
    cancelled.
    """
    today = today or date.today()
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)
