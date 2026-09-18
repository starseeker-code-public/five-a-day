"""Symmetric encryption for secrets that must survive in the database.

Exists for exactly one value today: the Google Drive refresh token. CLAUDE.md's
rule is that OAuth is identity-only and the callback keeps NOTHING, and the
reason is concrete — the old flow put access token, refresh token and client
secret into ``request.session``, which is the DATABASE backend, so all three sat
in ``django_session`` rows as signed-but-unencrypted base64 and were captured by
every Cloud SQL backup and by ``scripts/export_prod_db.sh``.

Storing a long-lived Drive credential re-opens that question, so it is answered
rather than ignored: the token is Fernet-encrypted with a key **derived from
``SECRET_KEY``**, which lives in Secret Manager and is not in the database. A
stolen dump is therefore not enough on its own.

Deriving from ``SECRET_KEY`` rather than adding a new env var is deliberate.
There is no second secret to provision on the service AND the 12 jobs (the
`ACADEMY_IBAN_HOLDER` lesson), no key to lose, and the failure mode of a
``SECRET_KEY`` rotation is benign and self-announcing: the token stops
decrypting, the app reports Drive as disconnected, and an admin clicks connect
again. HKDF is used rather than the raw key so this ciphertext cannot be
confused with, or used to attack, anything else signed by the same secret.

Stdlib + ``cryptography`` only (a declared dependency): no models, no Django
app-registry access, so it can be imported from anywhere.
"""

from __future__ import annotations

import base64
import logging

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

logger = logging.getLogger(__name__)

#: Domain separation for the derived key. Changing this invalidates every
#: existing ciphertext, which for the Drive token means "reconnect" — safe, but
#: do not change it casually.
_HKDF_INFO = b"fiveaday.token_crypto.v1"


def _fernet() -> Fernet:
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    ).derive(settings.SECRET_KEY.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(plaintext: str) -> str:
    """Return ``plaintext`` as an opaque token safe to store in a column."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Return the plaintext, or ``""`` when it cannot be recovered.

    Returning empty rather than raising is the point: the only realistic cause
    is a rotated ``SECRET_KEY``, and the honest consequence of that is "the
    credential is gone, ask for it again" — not a 500 on whatever page happened
    to read it. The caller decides what missing means; it is logged here once so
    the cause is never a mystery.
    """
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        logger.warning(
            "A stored secret could not be decrypted; treating it as absent. "
            "The usual cause is a rotated SECRET_KEY — reconnect the integration."
        )
        return ""


__all__ = ["decrypt_secret", "encrypt_secret"]
