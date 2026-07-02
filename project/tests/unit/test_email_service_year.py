"""EmailService context regression: `year` used to be hard-coded to 2025."""

from datetime import date
from unittest.mock import patch

import pytest

from comms.services.email_service import EmailService

pytestmark = pytest.mark.django_db


class TestEmailServiceYearDefault:
    def _spy_context(self, service, template):
        """Render `template` via the service, capture the context dict that
        actually reached `render_to_string`, and return it."""
        captured = {}

        def _fake_render(template_path, context):
            captured.update(context)
            return "<html>ok</html>"

        with patch("comms.services.email_service.render_to_string", side_effect=_fake_render):
            # `.send()` on the underlying EmailMessage still tries SMTP; stub it.
            with patch("django.core.mail.EmailMultiAlternatives.send", return_value=1):
                service.send_email(
                    template_name=template,
                    recipients="a@b.com",
                    subject="ping",
                    context={},
                    fail_silently=True,
                )
        return captured

    def test_year_defaults_to_current_year_not_2025(self):
        ctx = self._spy_context(EmailService(), "welcome_student")
        assert ctx["year"] == date.today().year, (
            "EmailService used to hard-code year=2025 which quietly backdated every "
            "email footer / tax certificate render — regression guard."
        )

    def test_year_can_still_be_overridden(self):
        service = EmailService()
        captured = {}

        def _fake_render(template_path, context):
            captured.update(context)
            return "<html>ok</html>"

        with patch("comms.services.email_service.render_to_string", side_effect=_fake_render):
            with patch("django.core.mail.EmailMultiAlternatives.send", return_value=1):
                service.send_email(
                    template_name="welcome_student",
                    recipients="a@b.com",
                    subject="ping",
                    context={"year": 1999},
                    fail_silently=True,
                )
        assert captured["year"] == 1999  # caller's explicit value wins

    def test_site_name_still_defaulted(self):
        ctx = self._spy_context(EmailService(), "welcome_student")
        assert ctx["site_name"] == "Five a Day"
