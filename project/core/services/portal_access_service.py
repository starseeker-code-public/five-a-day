"""Parent-portal ACCESS emails (v1.29.5).

The three things that can put a live credential in a family's inbox — the
once-only invitation, the admin's "Reenviar invitacion" action and the
self-service recovery form — plus the cooldown that stops an anonymous caller
rotating a family's temporary password on demand.

**Why they are here and not in `core/views/parent_portal.py`, where they used to
live.** Two of the three callers are not views: `students/admin.py`'s resend
action, and `ParentCreateView` via `core/views/parents.py`. The admin had to
import out of a view module to send an email — the wrong direction, and it put
a view module's internals on the critical path of an admin action. Issuing an
access credential is a service concern; the portal view is one caller of it.

They still take `request`, and that is deliberate rather than leftover: the mail
carries an absolute login URL, and `request.build_absolute_uri` is the only
correct source for the host it should point at.

PARENT PORTAL KILL SWITCH: `settings.PARENT_PORTAL_ENABLED` is read in BOTH
public functions here, which is two of the exactly three places in the app that
read it (the third is `SimpleAuthMiddleware._portal_gate`). Neither check is
redundant — see the comments inline and the gotcha in CLAUDE.md.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from comms.tasks import send_parent_temporary_password_task

logger = logging.getLogger(__name__)


#: How long an outstanding temporary password shields the family from having it
#: rotated by the UNAUTHENTICATED recovery form. See
#: `send_portal_temporary_password`.
PORTAL_TEMPORARY_PASSWORD_COOLDOWN = timedelta(minutes=15)


def _has_fresh_temporary_password(parent) -> bool:
    """True while this family's outstanding temporary password is recent.

    A temporary password does not expire (by design — an expiring credential is
    what this flow exists to remove), so "fresh" here is only about how recently
    it was ISSUED, and it is used to decide whether an unauthenticated caller
    may replace it.
    """
    if not parent.temporary_password or parent.temporary_password_issued_at is None:
        return False
    return timezone.now() - parent.temporary_password_issued_at < PORTAL_TEMPORARY_PASSWORD_COOLDOWN


def send_portal_temporary_password(request, parent, *, reset: bool = False, respect_cooldown: bool = False) -> bool:
    """
    Queue an email carrying a freshly generated temporary password.

    Shared by the once-only invitation (fired when the parent record is
    created), the admin's "Reenviar invitación" action, and the self-service
    recovery form, because they differ only in the copy.

    The password is generated INSIDE the task, not here: the plaintext is a live
    credential, and a task argument is serialised into the broker (Redis, in
    development) and shows up in task logs. Generating it at the point of use
    keeps it in one function and out of every queue and log line.

    `respect_cooldown` is for UNAUTHENTICATED callers, and only the recovery
    form passes it. Issuing a new temporary password INVALIDATES the previous
    one (`issue_temporary_password` overwrites the hash), so an attacker who
    knows a family's address could replay the recovery form and keep the
    credential in that family's inbox permanently stale — a denial of the
    recovery path itself, by an anonymous request, indefinitely. With the
    cooldown, one address can be rotated at most once per
    `PORTAL_TEMPORARY_PASSWORD_COOLDOWN` no matter how many IPs the requests
    come from, so the newest email in the family's mailbox stays valid long
    enough to be typed in. The family loses nothing: the mail already sent IS
    the working credential, and a request inside the window is a no-op rather
    than an error, so the page still says "revisa tu email" — which is true.
    The ADMIN action and the invitation deliberately do NOT pass it: an admin on
    the phone with a family must be able to reissue immediately.

    Returns False when the parent has no address to write to, when the cooldown
    suppressed the reissue, or when the task could not be enqueued. Never
    raises — an SMTP problem must not break the enrolment this is a side effect
    of.
    """
    # PARENT PORTAL KILL SWITCH — see settings.PARENT_PORTAL_ENABLED.
    # While the portal is off no access email goes out at all: the invitation,
    # the admin's "Reenviar invitación" action and the self-service recovery
    # form all pass through here. Mailing a password for pages that 404 would
    # be worse than sending nothing. Flip the setting to True to restore it.
    if not getattr(settings, "PARENT_PORTAL_ENABLED", False):
        return False

    if not parent.email:
        return False

    if respect_cooldown and _has_fresh_temporary_password(parent):
        # No address in the log line — this code path is the enumeration
        # boundary (see `_parent_by_email`).
        logger.info("Parent portal: recovery within the cooldown, keeping the temporary password already issued")
        return False

    # Only the (non-secret) login URL crosses the task boundary — see the task's
    # docstring for why the password itself does not.
    login_url = request.build_absolute_uri(reverse("parent_portal_login"))

    try:
        send_parent_temporary_password_task.delay(parent.id, login_url, reset)
    except Exception:  # never fail the request over email
        logger.exception("Failed to enqueue portal password email for parent %d", int(parent.id))
        return False
    return True


def send_portal_invitation_once(request, parent) -> bool:
    """
    Send the portal invitation the FIRST time only.

    A family with three children goes through the enrolment flow three times
    and must still receive exactly one invitation, so the guard is a timestamp
    on the parent rather than a count of anything. It is stamped BEFORE the
    send is queued: a duplicate invite is worse than a missed one, because the
    missed one is recoverable from "¿Has olvidado tu contraseña?" while the
    duplicate is an unexplained second email about a family's payment history.
    """
    # PARENT PORTAL KILL SWITCH — see settings.PARENT_PORTAL_ENABLED.
    # Checked BEFORE the stamp below, deliberately: stamping while the portal is
    # off would burn each family's one-and-only invitation on an email that was
    # never sent, so turning the portal back on would leave every parent created
    # in the meantime silently uninvited.
    if not getattr(settings, "PARENT_PORTAL_ENABLED", False):
        return False

    if parent.portal_invite_sent_at is not None or not parent.email:
        return False

    parent.portal_invite_sent_at = timezone.now()
    parent.save(update_fields=["portal_invite_sent_at", "updated_at"])
    return send_portal_temporary_password(request, parent, reset=False)


__all__ = [
    "PORTAL_TEMPORARY_PASSWORD_COOLDOWN",
    "send_portal_invitation_once",
    "send_portal_temporary_password",
]
