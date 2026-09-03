"""
Set or clear QAConfiguration.ready_for_prod — the QA sign-off flag that
deploy-production.yml's preflight reads through /health/?deep=1 on testing.

The nightly testing deploy (deploy-testing.yml) runs `set_ready_for_prod off`
right after a new version lands, so every release starts locked and only the
"¿Listo para desplegar?" button on /testing/ unlocks it. The `on` argument
exists for manual repair (e.g. the button is unreachable but QA has signed off).

Usage:
    python manage.py set_ready_for_prod off
    python manage.py set_ready_for_prod on
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Set or clear the QA 'ready for production' sign-off flag"

    def add_arguments(self, parser):
        parser.add_argument(
            "state",
            choices=["on", "off"],
            help="'on' marks the deployed version ready for production; 'off' locks it",
        )

    def handle(self, *args, **options):
        from core.github_dispatch import notify_github_qa_signoff
        from core.models import QAConfiguration

        ready = options["state"] == "on"
        config = QAConfiguration.get_config()
        config.ready_for_prod = ready
        config.save()
        self.stdout.write(self.style.SUCCESS(f"ready_for_prod = {ready}"))

        # A manual `on` is still a sign-off, so it must arm the production
        # workflow the same way the /testing/ button does. Fail-soft: without a
        # token (or outside the testing env) the nightly re-trigger covers it.
        if ready and notify_github_qa_signoff():
            self.stdout.write("repository_dispatch enviado — 'Deploy production' se está re-evaluando ahora")
