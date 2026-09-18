"""Connect the receipt archive to Google Drive, once, from /management/.

WHY THIS EXISTS, AND WHY IT IS NOT THE LOGIN FLOW
-------------------------------------------------
A service account cannot file these receipts. Tested against the academy's real
folder on 2026-09-18, with the folder shared to it as Editor and a genuinely
drive-scoped token: every write returns ``403 storageQuotaExceeded`` — *"Service
Accounts do not have storage quota… use OAuth delegation instead."* A service
account owns whatever it uploads and has no Drive storage on a consumer account.
So the uploader has to be a real Google account, which is what this connects.

It is a SEPARATE flow from `google_oauth_redirect`, deliberately:

* Signing in must stay identity-only. Folding `drive` into `_GOOGLE_SCOPES`
  would ask **every** teacher for access to their Drive on every sign-in, to
  serve one account's archive — the exact overreach CLAUDE.md's "OAuth is
  identity-only" rule was written about.
* This flow wants `access_type="offline"` and `prompt="consent"`, because it
  needs a refresh token that outlives the session. The login flow deliberately
  wants neither: a refresh token is the one OAuth artefact worth stealing.
* Its own session keys, so a consent in one flow can never be mistaken for the
  other's.

The refresh token is stored encrypted (`core.token_crypto`) on the
`GoogleDriveCredential` singleton — not in the session, which is the database
and rides out in every backup.

OPERATIONAL NOTE: the callback URI below must be registered in the OAuth client
("Authorized redirect URIs") or Google refuses the round trip with
`redirect_uri_mismatch`. It is a different path from the login callback, so
adding this feature means adding that URI once per environment.
"""

from __future__ import annotations

import logging
import os
import urllib.parse

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.decorators import admin_required
from core.log_safe import safe_log
from core.models import GoogleDriveCredential

logger = logging.getLogger(__name__)

#: Identity is requested alongside Drive so the app can record WHICH account
#: consented. Without it the UI could only say "connected", and connecting the
#: wrong Google account — a teacher's personal one rather than the academy's —
#: would be invisible until receipts started landing in the wrong Drive.
DRIVE_OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    # `userinfo.profile` is requested even though nothing reads it: Google GRANTS
    # it automatically alongside openid/email, and oauthlib rejects a token whose
    # granted scopes differ from the requested ones — "Scope has changed from …
    # to …", raised AFTER a perfectly good 200 from the token endpoint. The
    # alternative doing the rounds is OAUTHLIB_RELAX_TOKEN_SCOPE=1, which is
    # worse: the check it switches off is exactly what catches a user who unticks
    # the Drive permission, which would otherwise store a credential that cannot
    # upload and fail much later, far from the cause. The login flow's
    # `_GOOGLE_SCOPES` already lists all three, which is why it never hit this.
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/drive",
]

_STATE_KEY = "drive_oauth_state"
_VERIFIER_KEY = "drive_oauth_code_verifier"


def drive_connect_available() -> bool:
    """Whether this environment can complete the Drive consent at all.

    Production and local development: yes. The QA VM: **no**, and that is not a
    policy choice — Google refuses to register a redirect URI that is plain HTTP
    on a raw IP, which is exactly what `http://34.26.130.187:8000/` is, so the
    round trip cannot finish there however the app behaves.

    Read in TWO places on purpose: `/management/` hides the card, and the three
    views below refuse outright. Hiding alone would leave a button-less URL that
    still works — the same "forgetting to keep something OUT grants it silently"
    shape that `NON_ADMIN_ALLOWED_URL_NAMES` and `@admin_required` are paired
    against. A control nobody can reach must not merely be invisible; it must
    not be there.
    """
    return not getattr(settings, "IS_TESTING_ENV", False)


def drive_callback_uri(request) -> str:
    """The redirect URI for THIS flow — distinct from the login callback."""
    explicit = os.getenv("GOOGLE_DRIVE_REDIRECT_URI")
    if explicit:
        return explicit
    return request.build_absolute_uri(reverse("drive_oauth_callback"))


def _build_drive_flow(client_id, client_secret, callback_uri, state=None):
    # Lazy like every other use of this stack in the app: googleapiclient /
    # google-auth-oauthlib arrive transitively via django-gsheets and are not
    # declared in pyproject.toml, so a module-level import turns a degraded
    # Google feature into an app that cannot boot.
    from google_auth_oauthlib.flow import Flow

    cfg = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [callback_uri],
        }
    }
    kwargs = {"scopes": DRIVE_OAUTH_SCOPES}
    if state:
        kwargs["state"] = state
    flow = Flow.from_client_config(cfg, **kwargs)
    flow.redirect_uri = callback_uri
    return flow


@require_http_methods(["GET"])
@admin_required
def drive_oauth_redirect(request):
    """Send an admin to Google to authorise the archive."""
    if not drive_connect_available():
        raise Http404
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        messages.error(request, "Google OAuth no está configurado.")
        return redirect("management")

    if settings.DEBUG:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    callback_uri = drive_callback_uri(request)
    flow = _build_drive_flow(client_id, client_secret, callback_uri)
    authorization_url, state = flow.authorization_url(
        # The whole point: a credential that keeps working when nobody is
        # logged in — the Stripe webhook and all 12 Cloud Run jobs complete
        # payments with no session behind them.
        access_type="offline",
        # Google returns a refresh token only on the FIRST consent for a
        # client/account pair unless re-consent is forced. Without this, a
        # reconnect after a revoke silently yields no refresh token and the
        # archive breaks again a few minutes later, when the access token
        # expires.
        prompt="consent",
        # NOT `include_granted_scopes`: incremental auth makes Google return
        # whatever this account has ever granted this client, so the granted set
        # stops being predictable and the scope check above becomes a coin flip.
        # This flow wants exactly its own four scopes.
    )
    request.session[_STATE_KEY] = state
    # PKCE: the verifier lives on THIS Flow instance and the callback builds a
    # fresh one. Carry it in the session, same as the login flow.
    request.session[_VERIFIER_KEY] = flow.code_verifier
    return redirect(authorization_url)


@require_http_methods(["GET"])
@admin_required
def drive_oauth_callback(request):
    """Store the refresh token for the account that just consented."""
    if not drive_connect_available():
        raise Http404
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        messages.error(request, "Google OAuth no está configurado.")
        return redirect("management")

    if settings.DEBUG:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    expected_state = request.session.pop(_STATE_KEY, None)
    received_state = request.GET.get("state")
    # Fail CLOSED on an empty expected state: `None == None` would otherwise
    # accept a callback for a flow this session never started. Same trap the
    # login callback's allow-list check was fixed for.
    if not expected_state or expected_state != received_state:
        logger.error("Drive OAuth state mismatch; refusing the callback.")
        messages.error(request, "❌ Estado OAuth inválido. Vuelve a intentarlo.")
        return redirect("management")

    callback_uri = drive_callback_uri(request)
    flow = _build_drive_flow(client_id, client_secret, callback_uri, state=expected_state)
    flow.code_verifier = request.session.pop(_VERIFIER_KEY, None)

    parsed = urllib.parse.urlparse(callback_uri)
    authorization_response = urllib.parse.urlunparse(parsed._replace(query=request.META.get("QUERY_STRING", "")))

    try:
        flow.fetch_token(authorization_response=authorization_response)
    except Exception:  # google-auth raises a wide, undocumented set; all mean "not connected"
        logger.exception("Drive OAuth fetch_token failed")
        messages.error(request, "❌ Error al obtener el token de Google.")
        return redirect("management")

    credentials = flow.credentials

    # No refresh token means the credential dies with the access token in an
    # hour, and the archive would appear to work and then stop. Refuse it now,
    # loudly, rather than store something that cannot do the job.
    if not credentials.refresh_token:
        logger.error("Drive OAuth returned no refresh token; refusing to store a short-lived credential.")
        messages.error(
            request,
            "❌ Google no devolvió un token permanente. Revoca el acceso de la app en tu cuenta de Google y vuelve a conectar.",
        )
        return redirect("management")

    account_email = ""
    try:
        id_info = id_token.verify_oauth2_token(credentials.id_token, google_requests.Request(), client_id)
        account_email = id_info.get("email", "") if id_info.get("email_verified") else ""
    except Exception:  # identity is a label here, not the authorisation
        logger.exception("Drive OAuth id_token verification failed; storing the credential without an address")

    config = GoogleDriveCredential.get_config()
    config.set_refresh_token(credentials.refresh_token)
    config.account_email = account_email
    config.connected_by = getattr(request.user, "email", "") or getattr(request.user, "username", "")
    config.connected_at = timezone.now()
    config.save()

    logger.info("Google Drive archive connected (account=%s)", safe_log(account_email or "unknown"))
    messages.success(request, f"✅ Google Drive conectado{f' como {account_email}' if account_email else ''}.")
    return redirect("management")


@require_http_methods(["POST"])
@admin_required
def drive_oauth_disconnect(request):
    """Forget the stored credential. Uploads stop; nothing already filed moves."""
    if not drive_connect_available():
        raise Http404
    config = GoogleDriveCredential.get_config()
    config.disconnect()
    logger.info("Google Drive archive disconnected")
    messages.success(request, "Google Drive desconectado. Los recibos ya archivados no se han tocado.")
    return redirect("management")
