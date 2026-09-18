"""The Drive archive runs on a DELEGATED Google account, not a service account.

Tested against the academy's real folder on 2026-09-18: a service account with
Editor on the folder and a genuinely drive-scoped token still gets

    403 storageQuotaExceeded
    "Service Accounts do not have storage quota... use OAuth delegation instead."

because a service account owns whatever it uploads and has no Drive storage on a
consumer account. So the uploader has to be a real account, connected once from
`/management/`. These tests pin the parts of that which are easy to get subtly
wrong and impossible to notice: the credential must not be readable from a
database dump, and the stored account must win over the service-account routes.
"""

import pytest
from django.utils import timezone

from core.models import GoogleDriveCredential
from core.token_crypto import decrypt_secret, encrypt_secret

pytestmark = pytest.mark.django_db


class TestTokenCrypto:
    def test_round_trip(self):
        assert decrypt_secret(encrypt_secret("1//0abcdEFGH")) == "1//0abcdEFGH"

    def test_ciphertext_does_not_contain_the_secret(self):
        """The whole point: a database dump must not yield the token. The
        original objection in CLAUDE.md was that OAuth material sat in
        `django_session` as plain base64 and rode out in every Cloud SQL backup."""
        secret = "1//0-super-secret-refresh-token"
        blob = encrypt_secret(secret)
        assert secret not in blob
        # ...and not merely encoded: base64 of the plaintext must not appear either.
        import base64

        assert base64.b64encode(secret.encode()).decode().rstrip("=") not in blob

    def test_two_encryptions_differ(self):
        """Fernet includes a random IV, so identical secrets must not produce
        identical ciphertext — otherwise the column leaks equality."""
        assert encrypt_secret("same") != encrypt_secret("same")

    def test_empty_is_passed_through(self):
        assert encrypt_secret("") == ""
        assert decrypt_secret("") == ""

    def test_undecryptable_reads_as_absent_rather_than_raising(self):
        """A rotated SECRET_KEY means "the credential is gone, ask again" — not
        a 500 on whatever page happened to read it."""
        assert decrypt_secret("not-a-fernet-token") == ""

    def test_a_key_change_invalidates_the_stored_secret(self, settings):
        blob = encrypt_secret("token")
        settings.SECRET_KEY = "a-completely-different-secret-key-value"
        assert decrypt_secret(blob) == ""


class TestGoogleDriveCredential:
    def test_is_a_singleton(self):
        first = GoogleDriveCredential.get_config()
        first.account_email = "archivo@fiveaday.test"
        first.save()
        second = GoogleDriveCredential.get_config()
        assert second.pk == first.pk == 1
        assert GoogleDriveCredential.objects.count() == 1

    def test_the_column_never_holds_the_plaintext(self):
        config = GoogleDriveCredential.get_config()
        config.set_refresh_token("1//0-refresh")
        config.save()

        raw = GoogleDriveCredential.objects.values_list("refresh_token_encrypted", flat=True).first()
        assert "1//0-refresh" not in raw
        assert config.refresh_token == "1//0-refresh"

    def test_not_connected_without_a_token(self):
        assert GoogleDriveCredential.get_config().is_connected is False

    def test_disconnect_forgets_everything_but_keeps_the_row(self):
        config = GoogleDriveCredential.get_config()
        config.set_refresh_token("1//0-refresh")
        config.account_email = "archivo@fiveaday.test"
        config.connected_at = timezone.now()
        config.save()

        config.disconnect()

        config.refresh_from_db()
        assert config.is_connected is False
        assert config.account_email == ""
        assert config.connected_at is None
        assert GoogleDriveCredential.objects.count() == 1

    def test_the_singleton_cannot_be_deleted(self):
        """Same guard as SiteConfiguration: overriding delete() alone leaves
        `objects.all().delete()` open, which is how a whole config row once went."""
        config = GoogleDriveCredential.get_config()
        # The call is deliberately NOT inside the assert: `python -O` strips
        # assert statements outright, so `assert config.delete() == ...` would
        # mean this test never attempts a delete and passes having proved
        # nothing about the guard it is named for (CodeQL py/side-effect-in-assert).
        deleted = config.delete()
        assert deleted == (0, {})
        assert GoogleDriveCredential.objects.count() == 1


class TestCredentialPrecedence:
    """The delegated account must beat both service-account routes.

    Getting this backwards is silent: the service account authenticates
    perfectly well and then fails at the upload with `storageQuotaExceeded`,
    which reads like a Drive outage rather than a wiring mistake.
    """

    def _service(self, settings):
        from core.services.drive_service import DriveReceiptService

        settings.GOOGLE_DRIVE_RECEIPTS_FOLDER_ID = "folder-1"
        settings.GOOGLE_CLIENT_ID = "cid"
        return DriveReceiptService()

    def test_a_connected_account_is_used_ahead_of_impersonation(self, settings, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
        settings.GOOGLE_DRIVE_IMPERSONATE_SA = "sa@example.iam.gserviceaccount.com"

        config = GoogleDriveCredential.get_config()
        config.set_refresh_token("1//0-refresh")
        config.save()

        creds = self._service(settings)._build_credentials()
        assert creds.refresh_token == "1//0-refresh"
        assert "drive" in " ".join(creds.scopes)

    def test_no_connection_means_no_delegated_credentials(self, settings, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
        assert self._service(settings)._delegated_credentials() is None

    def test_missing_oauth_client_means_no_delegated_credentials(self, settings, monkeypatch):
        """Without the client id/secret the refresh token cannot be exchanged,
        so returning a credential here would only fail later and further away."""
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

        config = GoogleDriveCredential.get_config()
        config.set_refresh_token("1//0-refresh")
        config.save()

        assert self._service(settings)._delegated_credentials() is None

    def test_a_connected_account_alone_counts_as_configured(self, settings, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
        settings.GOOGLE_DRIVE_IMPERSONATE_SA = ""

        config = GoogleDriveCredential.get_config()
        config.set_refresh_token("1//0-refresh")
        config.save()

        assert self._service(settings).is_configured() is True


class TestConnectIsAbsentOnTheQaVm:
    """The QA VM cannot complete the consent, so it must not offer it.

    Not a policy choice: Google refuses to register a redirect URI that is plain
    HTTP on a raw IP, which is exactly what the testing VM is. Both halves are
    pinned here because hiding alone would leave a working URL behind a missing
    button — the "forgetting to keep something OUT grants it silently" shape the
    whitelist and `@admin_required` are paired against.
    """

    def test_available_in_production(self, settings):
        settings.IS_TESTING_ENV = False
        from core.views.google_drive import drive_connect_available

        assert drive_connect_available() is True

    def test_available_in_local_development(self, settings):
        """Kept reachable locally on purpose: registering the localhost callback
        is how the flow gets exercised before it reaches the academy."""
        settings.IS_TESTING_ENV = False
        settings.ENVIRONMENT = "development"
        from core.views.google_drive import drive_connect_available

        assert drive_connect_available() is True

    def test_refused_on_the_qa_vm(self, settings):
        settings.IS_TESTING_ENV = True
        from core.views.google_drive import drive_connect_available

        assert drive_connect_available() is False

    @pytest.mark.parametrize(
        "url_name",
        ["drive_oauth_redirect", "drive_oauth_callback", "drive_oauth_disconnect"],
    )
    def test_every_endpoint_404s_on_the_qa_vm(self, settings, url_name):
        """The button is hidden there; the URL must not still work."""
        import inspect

        from django.http import Http404
        from django.test import RequestFactory

        from core.views import google_drive

        settings.IS_TESTING_ENV = True
        # `inspect.unwrap` follows the __wrapped__ chain, so this does not depend
        # on how many decorators the view happens to carry. The admin/method
        # decorators are not what is under test and would mask the guard.
        view = inspect.unwrap(getattr(google_drive, url_name))
        with pytest.raises(Http404):
            view(RequestFactory().get("/"))

    def test_a_teacher_never_sees_the_control(self, settings, authenticated_client, monkeypatch):
        """Connecting authorises the app against a real Google account — that is
        the academy's decision, not a teacher's. The control lives in the page
        header inside the `is_admin_user` gate; this pins that it disappears for
        a non-admin rather than merely looking disabled."""
        from django.urls import reverse

        settings.IS_TESTING_ENV = False
        monkeypatch.setattr("core.context_processors._is_non_admin_teacher", lambda request: True)

        html = authenticated_client.get(reverse("management")).content.decode()
        assert reverse("drive_oauth_redirect") not in html
        assert "Conectar Drive" not in html
        assert "Drive conectado" not in html

    def test_the_management_page_offers_the_button_only_where_it_can_work(self, settings, authenticated_client):
        """The template half of the same rule, asserted against real HTML.

        Both directions on purpose: asserting only the absence would pass just as
        happily if the page 500'd or the card had been deleted outright.
        """
        from django.urls import reverse

        settings.IS_TESTING_ENV = False
        html = authenticated_client.get(reverse("management")).content.decode()
        assert reverse("drive_oauth_redirect") in html

        settings.IS_TESTING_ENV = True
        html = authenticated_client.get(reverse("management")).content.decode()
        assert reverse("drive_oauth_redirect") not in html


class TestHomeWarnsWhileDriveIsDisconnected:
    """Home nags an admin on EVERY visit while the archive is off.

    Not dismissible-once on purpose: the symptom of Drive being disconnected is
    silence — receipts are simply never filed and nothing errors — so a banner
    that could be dismissed for good would be forgotten exactly as easily as the
    problem it reports.
    """

    def _home(self, client):
        from django.urls import reverse

        return client.get(reverse("home")).content.decode()

    def test_warns_when_not_connected(self, settings, authenticated_client):
        settings.IS_TESTING_ENV = False
        html = self._home(authenticated_client)
        assert "drive-warning-modal" in html
        from django.urls import reverse

        assert reverse("management") in html

    def test_silent_once_connected(self, settings, authenticated_client):
        settings.IS_TESTING_ENV = False
        config = GoogleDriveCredential.get_config()
        config.set_refresh_token("1//0-refresh")
        config.save()
        assert "drive-warning-modal" not in self._home(authenticated_client)

    def test_it_fires_again_on_the_next_visit(self, settings, authenticated_client):
        """The whole point of rendering it server-side: no localStorage, no
        cookie, nothing a closed tab can remember."""
        settings.IS_TESTING_ENV = False
        assert "drive-warning-modal" in self._home(authenticated_client)
        assert "drive-warning-modal" in self._home(authenticated_client)

    def test_teachers_are_not_nagged(self, settings, authenticated_client, monkeypatch):
        """Only an admin can connect it, so warning anyone else is noise about a
        control they cannot reach."""
        settings.IS_TESTING_ENV = False
        monkeypatch.setattr("core.middleware._is_non_admin_teacher", lambda request: True)
        assert "drive-warning-modal" not in self._home(authenticated_client)

    def test_not_shown_where_connecting_is_impossible(self, settings, authenticated_client):
        """The QA VM cannot complete the consent, so telling QA to go and connect
        it would send them at a button that is not there."""
        settings.IS_TESTING_ENV = True
        assert "drive-warning-modal" not in self._home(authenticated_client)
