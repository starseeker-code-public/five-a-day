"""Reset 2FA for a locked-out admin — invoked via `manage.py`.

Only usable from the server console (SSH into the VM, or `gcloud run jobs
execute` in production). Wipes the secret + backup codes and turns
`two_factor_enabled` off so the user can re-enrol from the setup page.
"""

from django.core.management.base import BaseCommand, CommandError

from core.services.two_factor_service import disable
from students.models import Teacher


class Command(BaseCommand):
    help = "Reset 2FA for a Teacher by email. The user re-enrols via /two-factor/setup/ after next login."

    def add_arguments(self, parser):
        parser.add_argument("email", type=str, help="Email of the Teacher whose 2FA should be reset.")

    def handle(self, *args, **opts):
        email = opts["email"].strip().lower()
        try:
            teacher = Teacher.objects.get(email__iexact=email)
        except Teacher.DoesNotExist as e:
            raise CommandError(f"No Teacher with email {email!r}") from e

        was_enabled = teacher.two_factor_enabled
        disable(teacher)
        self.stdout.write(
            self.style.SUCCESS(
                f"✓ 2FA reset for {teacher.full_name} ({teacher.email}) — "
                f"was_enabled={was_enabled}, now the user can re-enrol from /two-factor/setup/."
            )
        )
