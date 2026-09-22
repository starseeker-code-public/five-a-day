"""
Rebuild the public tester sandbox: re-seed the data, then repair what the seeders
cannot reach.

WHY A COMMAND AND NOT A SHELL SCRIPT ON THE VM
----------------------------------------------
It runs unattended, nightly, against a database people are looking at, and the
interesting half of it (the credential repair, the price restore) is ORM work a
shell script could only do by shelling back into Django anyway. As a command it
is importable by the Beat task, runnable by hand during an incident, and
coverable by tests.

WHAT THE SEEDERS ALREADY DO, AND WHAT THEY MISS
-----------------------------------------------
``seed_testdata --reset`` wipes and rebuilds the people and the money —
HistoryLog, TodoItem, ScheduleSlot, FunFridayAttendance, Expense, Payment,
Enrollment, StudentParent, Student, Parent, Group. That is most of the sandbox,
and it is why a tester visitor may freely destroy any of it.

It does NOT touch four things a tester session can still reach and change, and
each one would therefore drift PERMANENTLY, one visitor at a time:

  1. ``SiteConfiguration`` — the price list. Editing it is one of the better
     things to demonstrate (it is the app's single source of truth for every
     fee), so it is deliberately NOT on the denylist; restoring it here is what
     keeps that decision affordable.
  2. The tester account's own PASSWORD and second factor. `change_password` and
     both 2FA endpoints are outside `TESTER_ALLOWED_URL_NAMES`, so this is a
     backstop
     rather than the primary control — but it is the backstop that makes the
     primary control's failure survivable, because every other route to a
     changed password (an admin in ``/admin/``, a ``manage.py`` invocation, a
     future endpoint nobody remembered to deny) ends in a portfolio link that
     logs nobody in and no automated way back.
  3. Unsent ``FunFridayScheduledSend`` rows. ``fun_friday_form`` IS in the tester
     allowlist, so a visitor can queue an announcement; the
     row is drained by Beat at 14:30, long after the request ended — exactly the
     class of side effect no request-scoped guard can catch.
  4. The tester account's own sessions, so the nightly reset is also a logout and
     two visitors on consecutive days never share one session's state.

``BacklogTask`` / ``Feature`` are deliberately NOT cleaned: every QA endpoint is
``@qa_access_required``, which ``may_use_qa_tools`` denies to tester accounts, so
a tester session cannot create one. Sweeping for rows it cannot produce would put
QA's real backlog at risk for no benefit.

SAFETY
------
Refuses under ``DJANGO_ENV=production`` (like ``seed_demo_parents``), and the
Beat task that calls it is additionally gated on ``IS_TESTING_ENV`` — because
Beat also runs in DEVELOPMENT, where an ungated nightly entry would quietly wipe
every developer's local database. Production has no Beat at all and must never
get a Cloud Run Job for this; that is a knowing exception to the "every Beat
task needs a wrapper command with a Cloud Scheduler entry" convention, and the
exception is the entire point.
"""

import os

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from billing import constants
from billing.models import SiteConfiguration
from core.models import FunFridayScheduledSend
from students.models import Teacher

#: The price fields restored to their seed values, and the constant each comes
#: from. Deliberately the SAME mapping ``SiteConfiguration.get_config()`` uses to
#: create the row in the first place — "restore" and "create" have to mean the
#: same thing, or a reset leaves the sandbox at prices the app would never have
#: started with.
_PRICE_DEFAULTS = {
    "children_enrollment_fee": constants.CHILDREN_ENROLLMENT_FEE,
    "adult_enrollment_fee": constants.ADULT_ENROLLMENT_FEE,
    "full_time_monthly_fee": constants.FULL_TIME_MONTHLY_FEE,
    "part_time_monthly_fee": constants.PART_TIME_MONTHLY_FEE,
    "part_time_child_monthly_fee": constants.PART_TIME_CHILD_MONTHLY_FEE,
    "adult_group_monthly_fee": constants.ADULT_GROUP_MONTHLY_FEE,
    "language_cheque_discount": constants.LANGUAGE_CHEQUE_DISCOUNT[0],
    "quarterly_enrollment_discount": constants.QUARTERLY_ENROLLMENT_DISCOUNT[0],
    "june_discount": constants.JUNE_DISCOUNT[0],
    "sibling_discount": constants.SIBLING_DISCOUNT[0],
    "returning_student_enrollment_discount": constants.RETURNING_STUDENT_ENROLLMENT_DISCOUNT,
}


def seeded_passwords_by_email() -> dict[str, str]:
    """``{email: password}`` for every TEACHER_SEED_<N>_* block defining both.

    Iterates the blocks exactly as ``seed_teachers`` does — stop at the first gap
    in FIRST_NAME — so the two cannot disagree about which blocks exist. Keeping
    the tester credential in that one env contract is deliberate: a second place to
    define it is a second place for it to drift from what the portfolio prints.
    """
    found: dict[str, str] = {}
    index = 1
    while True:
        prefix = f"TEACHER_SEED_{index}_"
        if not os.getenv(f"{prefix}FIRST_NAME"):
            break
        email = (os.getenv(f"{prefix}EMAIL") or "").strip().lower()
        password = os.getenv(f"{prefix}PASSWORD")
        if email and password:
            found[email] = password
        index += 1
    return found


class Command(BaseCommand):
    help = "Rebuild the public tester sandbox (never production). Re-seeds data and repairs the tester account."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-seed",
            action="store_true",
            help=(
                "Repair the tester account, prices and queued sends WITHOUT re-seeding. "
                "For fixing a locked-out tester login mid-day, when wiping the data "
                "somebody is currently looking at would be rude."
            ),
        )

    def handle(self, *args, **options):
        if settings.ENVIRONMENT == "production":
            raise CommandError(
                "reset_tester_environment refuses to run in production - it wipes every "
                "Student, Parent, Payment, Enrollment, Group and Expense."
            )

        if not options["skip_seed"]:
            self._reseed()

        self._restore_prices()
        self._repair_tester_accounts()
        self._drain_queued_sends()

        self.stdout.write(self.style.SUCCESS("Tester sandbox rebuilt."))

    # -- steps ---------------------------------------------------------------

    def _reseed(self):
        self.stdout.write("Re-seeding the QA dataset...")
        call_command("seed_testdata", reset=True, stdout=self.stdout)

        # Best-effort for the same reason `api_seed_database` treats the demo
        # family that way: the dataset is the point, and a missing env block for
        # a bonus fixture must not fail the whole nightly run.
        for command in ("seed_demo_parents", "seed_teachers"):
            try:
                call_command(command, stdout=self.stdout)
            except Exception as exc:  # noqa: BLE001 - reported, never fatal to the reset
                self.stdout.write(self.style.WARNING(f"{command} failed: {exc.__class__.__name__}"))

    def _restore_prices(self):
        config = SiteConfiguration.get_config()
        changed = [field for field, default in _PRICE_DEFAULTS.items() if getattr(config, field) != default]
        if not changed:
            self.stdout.write("Prices already at seed values.")
            return

        for field in changed:
            setattr(config, field, _PRICE_DEFAULTS[field])
        # `full_clean()` because `update_site_config` validates on the way in and
        # a restore has to clear the same bar - neither `create()` nor `save()`
        # validates on its own.
        config.full_clean()
        config.save()
        self.stdout.write(self.style.SUCCESS(f"Restored {len(changed)} price field(s): {', '.join(changed)}"))

    def _repair_tester_accounts(self):
        """Put every tester account back to the credential the env advertises."""
        tester_teachers = list(Teacher.objects.filter(tester=True))
        if not tester_teachers:
            self.stdout.write("No tester account configured (no Teacher with tester=True).")
            return

        passwords = seeded_passwords_by_email()
        repaired = 0

        for teacher in tester_teachers:
            dirty = False
            # A visitor cannot reach the 2FA endpoints, but an enrolled second
            # factor on a SHARED public account locks out everyone at once and
            # cannot be undone from the UI, so it is cleared unconditionally
            # rather than only when we think something went wrong.
            if teacher.two_factor_enabled or teacher.two_factor_secret:
                teacher.two_factor_enabled = False
                teacher.two_factor_secret = ""
                teacher.two_factor_backup_codes = []
                teacher.two_factor_last_counter = None
                dirty = True
            if not teacher.active:
                teacher.active = True
                dirty = True
            if dirty:
                teacher.save()

            password = passwords.get(teacher.email.lower())
            if password:
                # `ensure_user(password=...)` sets it unconditionally, which is
                # the point: `seed_teachers` only overwrites a password for tester
                # rows, and `--skip-seed` does not run it at all.
                teacher.ensure_user(password=password)
                repaired += 1
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"No TEACHER_SEED_*_PASSWORD found for tester account {teacher.email} - "
                        "its password cannot be restored automatically."
                    )
                )

        self._end_tester_sessions(tester_teachers)

        if repaired:
            self.stdout.write(self.style.SUCCESS(f"Repaired {repaired} tester account(s)."))

    @staticmethod
    def _end_tester_sessions(tester_teachers):
        """Log every tester visitor out, so a reset is a clean slate for the next one.

        Sessions are opaque rows, so they are decoded rather than filtered — the
        table is small on this VM and this runs once a night. Only sessions whose
        Django auth id belongs to a tester account are dropped: a QA tester signed
        in beside the tester must not be logged out by it.
        """
        tester_user_ids = {str(t.user_id) for t in tester_teachers if t.user_id}
        if not tester_user_ids:
            return

        doomed = [
            session.session_key
            for session in Session.objects.iterator()
            if session.get_decoded().get("_auth_user_id") in tester_user_ids
        ]
        if doomed:
            Session.objects.filter(session_key__in=doomed).delete()

    def _drain_queued_sends(self):
        """Discard announcements queued but not yet sent.

        `sent_at__isnull=True` is the same predicate the drain task uses to decide
        what it still owes, so this removes exactly the backlog that would
        otherwise fire at 14:30 for data that no longer exists.
        """
        deleted, _ = FunFridayScheduledSend.objects.filter(sent_at__isnull=True).delete()
        if deleted:
            self.stdout.write(self.style.SUCCESS(f"Discarded {deleted} unsent scheduled announcement(s)."))
