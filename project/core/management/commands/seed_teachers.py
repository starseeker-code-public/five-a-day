"""
Idempotently seed Teacher + linked auth.User from TEACHER_SEED_<N>_* env vars.

Intended to run on container start for the testing and production environments
(see entrypoint.sh). In development no teachers are seeded — the dev
environment uses env-var basic-auth, not Teacher login.

Env var contract (N starts at 1, iteration stops at the first missing FIRST_NAME):
    TEACHER_SEED_<N>_FIRST_NAME     required
    TEACHER_SEED_<N>_LAST_NAME      required
    TEACHER_SEED_<N>_EMAIL          required  (the Teacher's real email address)
    TEACHER_SEED_<N>_USERNAME       optional — login id for the linked auth.User.
                                    Defaults to EMAIL. Set it to a short handle
                                    ("claudia") when typing a full address at the
                                    login box is a nuisance; the email still works
                                    as a login, because `login_view` falls back to
                                    an email lookup when the handle does not match.
    TEACHER_SEED_<N>_PHONE          optional
    TEACHER_SEED_<N>_ADMIN          optional, default False  ("True"/"1"/"yes")
    TEACHER_SEED_<N>_PASSWORD       optional — if set, the linked User is activated
                                    with this password; if absent, the user gets an
                                    unusable password and must use /password-reset/.

The command is idempotent: re-running it updates the Teacher's name/phone/admin
flags if they changed, and keeps the linked User in sync, but does NOT rewrite
the password on subsequent runs (unless the user still has no usable password).
"""

import os

from django.core.management.base import BaseCommand
from django.db import transaction

from students.models import Teacher


def _env_bool(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().lower() in ("true", "1", "yes", "y", "t")


class Command(BaseCommand):
    help = "Seed Teacher + linked auth.User rows from TEACHER_SEED_<N>_* env vars."

    def handle(self, *args, **options):
        seeded = 0
        index = 1

        while True:
            prefix = f"TEACHER_SEED_{index}_"
            first_name = os.getenv(f"{prefix}FIRST_NAME")
            if not first_name:
                break  # Stop at first gap.

            last_name = os.getenv(f"{prefix}LAST_NAME", "").strip()
            email = os.getenv(f"{prefix}EMAIL", "").strip()

            if not last_name or not email:
                self.stdout.write(self.style.WARNING(f"⏭️  Skipping {prefix}* — LAST_NAME and EMAIL are required."))
                index += 1
                continue

            phone = os.getenv(f"{prefix}PHONE", "").strip()
            login_username = os.getenv(f"{prefix}USERNAME", "").strip()
            is_admin = _env_bool(os.getenv(f"{prefix}ADMIN"))
            password = os.getenv(f"{prefix}PASSWORD") or None

            with transaction.atomic():
                teacher, created = Teacher.objects.get_or_create(
                    email=email,
                    defaults={
                        "first_name": first_name.strip(),
                        "last_name": last_name,
                        "phone": phone,
                        "active": True,
                        "admin": is_admin,
                    },
                )

                if not created:
                    dirty = False
                    if teacher.first_name != first_name.strip():
                        teacher.first_name = first_name.strip()
                        dirty = True
                    if teacher.last_name != last_name:
                        teacher.last_name = last_name
                        dirty = True
                    if teacher.phone != phone:
                        teacher.phone = phone
                        dirty = True
                    if teacher.admin != is_admin:
                        teacher.admin = is_admin
                        dirty = True
                    if not teacher.active:
                        teacher.active = True
                        dirty = True
                    if dirty:
                        teacher.save()

                # Only set the password on first creation, or if the linked user
                # still has no usable password. Re-running the command should not
                # silently reset a password an admin changed later.
                set_pw = None
                if password:
                    if teacher.user_id is None:
                        set_pw = password
                    elif not teacher.user.has_usable_password():
                        set_pw = password
                user = teacher.ensure_user(password=set_pw)

                # `ensure_user` keys the auth.User on the email. A short login
                # handle is applied afterwards, and survives: both `ensure_user`
                # and the Teacher post_save signal only rewrite `username` when
                # the *email* changed, which it has not.
                if login_username and user.username != login_username:
                    # A handle already taken by another account is a config
                    # error in the env file, not a reason to abort the boot:
                    # `seed_teachers` runs from entrypoint.sh and the teacher
                    # can still log in with their email.
                    from django.contrib.auth import get_user_model

                    clash = get_user_model().objects.filter(username=login_username).exclude(pk=user.pk).exists()
                    if clash:
                        self.stdout.write(
                            self.style.WARNING(
                                f"⚠️  {prefix}USERNAME='{login_username}' is already taken — "
                                f"keeping '{user.username}' as the login for {email}."
                            )
                        )
                        login_username = ""
                    else:
                        user.username = login_username
                        user.save(update_fields=["username"])

                status = "created" if created else "updated"
                role = "admin" if is_admin else "non-admin"
                login_as = f" login: {user.username}" if login_username else ""
                self.stdout.write(
                    self.style.SUCCESS(f"✅ Teacher {status}: {teacher.full_name} <{email}> ({role}){login_as}")
                )
                seeded += 1

            index += 1

        if seeded == 0:
            self.stdout.write(
                self.style.WARNING(
                    "No TEACHER_SEED_1_FIRST_NAME found — nothing to seed. "
                    "Set TEACHER_SEED_<N>_* env vars in .env.testing or .env.production."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS(f"Seeded {seeded} teacher(s)."))
