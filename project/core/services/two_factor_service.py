"""
TOTP-based two-factor authentication for Teacher accounts.

Uses:
- `pyotp` for TOTP secret generation + verification (RFC 6238)
- `qrcode` for the enrolment QR code

Design notes
- The shared secret is stored plain in `Teacher.two_factor_secret`. That's
  how every TOTP library exposes it — the "secret" is meant to be readable
  by both server and authenticator app. If the DB is compromised the
  attacker has your secrets anyway; adding an extra layer of encryption
  here is theatre.
- Backup codes are stored with Django's password hasher and carry 64 bits of
  entropy. They were 32 bits (8 hex chars) behind a single unsalted sha256
  until v1.23.0, which is a seconds-long GPU job over the whole keyspace — so
  anyone who could read the table recovered all 8 codes for every admin and had
  a permanent 2FA bypass. The reasoning above about the TOTP secret does NOT
  transfer to the backup codes: the secret is shared with the authenticator by
  design, whereas the codes exist precisely to survive a compromise.
  Losing the phone + all backup codes = admin password +
  `manage.py reset_two_factor <email>` recovery.
- Only admins (`Teacher.admin=True`) are prompted for 2FA at login. Non-
  admin teachers don't have access to sensitive endpoints anyway.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import time
from dataclasses import dataclass

import pyotp
import qrcode
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password

_ISSUER = "Five a Day"
_BACKUP_CODE_COUNT = 8
# One step (30 s) either side of now, so a code lives for at most ~90 s.
_TOTP_VALID_WINDOW = 1


@dataclass(frozen=True)
class EnrolmentPayload:
    """Everything the setup page needs to render the QR + confirmation form."""

    secret: str
    provisioning_uri: str
    qr_png_base64: str
    backup_codes: list[str]  # plaintext, shown ONCE at enrolment


def _normalise_code(code: str) -> str:
    """Canonical form for comparison — codes are shown uppercase and users
    retype them with stray spaces or lowercase."""
    return code.strip().upper().replace(" ", "").replace("-", "")


def _hash_code(code: str) -> str:
    """Hash a backup code with Django's configured password hasher (PBKDF2).

    Salted and deliberately slow, unlike the bare sha256 this replaced. A
    backup code is a bearer credential that bypasses the second factor, so it
    warrants the same treatment as a password.
    """
    return make_password(_normalise_code(code))


def _is_legacy_hash(stored: str) -> bool:
    """True for the pre-v1.23.0 format: a bare 64-char sha256 hex digest.

    Django hashes always carry an `algorithm$...` prefix, so the two are
    unambiguous. Accepting the old format for one release means existing admins
    keep working codes instead of being locked out of their own recovery path.
    """
    return len(stored) == 64 and all(c in "0123456789abcdef" for c in stored.lower())


def _legacy_digest(code: str) -> str:
    return hashlib.sha256(_normalise_code(code).encode("utf-8")).hexdigest()


def _issuer_name() -> str:
    """Include the environment in the issuer so a dev + prod entry in the
    authenticator app don't collide on the phone."""
    env = getattr(settings, "ENVIRONMENT", "development")
    if env == "production":
        return _ISSUER
    return f"{_ISSUER} ({env})"


def generate_backup_codes(n: int = _BACKUP_CODE_COUNT) -> list[str]:
    """Generate `n` single-use codes (16 hex chars uppercase = 64 bits).

    Was `token_hex(4)` — 32 bits — which is exhaustible offline. 64 bits is not,
    and grouped into blocks of four it is still transcribable by hand.

    Returned in plaintext so the caller can show them exactly once; only the
    hashes are persisted (see `_hash_code`).
    """
    return [secrets.token_hex(8).upper() for _ in range(n)]


def begin_enrolment(teacher) -> EnrolmentPayload:
    """
    Generate a fresh secret + backup codes for a teacher and stage them on
    the Teacher row. Does NOT flip `two_factor_enabled` — the user must
    confirm the setup by entering a valid TOTP first (see `confirm_enrolment`).
    """
    secret = pyotp.random_base32()
    codes = generate_backup_codes()

    teacher.two_factor_secret = secret
    teacher.two_factor_backup_codes = [_hash_code(c) for c in codes]
    teacher.two_factor_enabled = False  # awaiting confirmation
    teacher.save(
        update_fields=[
            "two_factor_secret",
            "two_factor_backup_codes",
            "two_factor_enabled",
            "updated_at",
        ]
    )

    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(name=teacher.email, issuer_name=_issuer_name())

    img = qrcode.make(provisioning_uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_base64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return EnrolmentPayload(
        secret=secret,
        provisioning_uri=provisioning_uri,
        qr_png_base64=qr_base64,
        backup_codes=codes,
    )


def confirm_enrolment(teacher, code: str) -> bool:
    """
    Flip `two_factor_enabled=True` iff `code` verifies against the staged
    secret. Called from the setup view after the user scans the QR and
    types a 6-digit code.

    Returns True on success, False on bad code / no secret staged.
    """
    if not teacher.two_factor_secret:
        return False
    if not verify_totp(teacher, code):
        return False
    teacher.two_factor_enabled = True
    teacher.save(update_fields=["two_factor_enabled", "updated_at"])
    return True


def verify_totp(teacher, code: str) -> bool:
    """Return True iff `code` is a valid, not-yet-used TOTP for this teacher.

    Accepts the current 30-second step plus one either side (`valid_window=1`)
    so a slightly-off clock still works — which also means a single code stays
    valid for up to ~90 seconds. RFC 6238 §5.2 requires rejecting a code that
    has already been accepted, otherwise anyone who observes one (shoulder-surf,
    a screenshot in a support ticket, a proxy) can reuse it inside that window.

    `two_factor_last_counter` records the highest time-step accepted, and a step
    at or below it is refused. Steps are monotonic, so this also invalidates any
    earlier code the window would otherwise still admit.
    """
    if not teacher.two_factor_secret:
        return False
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False

    totp = pyotp.TOTP(teacher.two_factor_secret)
    if not totp.verify(code, valid_window=_TOTP_VALID_WINDOW):
        return False

    # Identify WHICH step matched so it can be burned. pyotp reports only
    # pass/fail, so re-check each candidate step in the accepted window.
    now = int(time.time())
    step = totp.interval
    matched_counter = None
    for offset in range(-_TOTP_VALID_WINDOW, _TOTP_VALID_WINDOW + 1):
        at = now + offset * step
        if hmac.compare_digest(totp.at(at), code):
            matched_counter = at // step
            break

    if matched_counter is None:  # pragma: no cover - verify() already passed
        return False

    last = teacher.two_factor_last_counter
    if last is not None and matched_counter <= last:
        return False

    teacher.two_factor_last_counter = matched_counter
    teacher.save(update_fields=["two_factor_last_counter", "updated_at"])
    return True


def verify_backup_code(teacher, code: str) -> bool:
    """
    Verify a one-time backup code. On success, REMOVES the used code from
    the stored list (backup codes are single-use).

    Accepts both the current hasher format and the legacy bare-sha256 digests,
    so admins enrolled before v1.23.0 are not locked out. A matched legacy code
    is consumed like any other, so the old format drains itself as codes are
    used; `manage.py reset_two_factor <email>` re-issues a full set in the new
    format.
    """
    candidate = _normalise_code(code or "")
    if not candidate:
        return False

    codes = list(teacher.two_factor_backup_codes or [])
    legacy = _legacy_digest(candidate)

    matched = None
    for stored in codes:
        if _is_legacy_hash(stored):
            # Both operands are fixed-length hex digests of the same function.
            if hmac.compare_digest(stored, legacy):
                matched = stored
                break
        elif check_password(candidate, stored):
            matched = stored
            break

    if matched is None:
        return False

    codes.remove(matched)
    teacher.two_factor_backup_codes = codes
    teacher.save(update_fields=["two_factor_backup_codes", "updated_at"])
    return True


def verify_code(teacher, code: str) -> bool:
    """Try TOTP first, fall back to backup code. Used by the login gate."""
    return verify_totp(teacher, code) or verify_backup_code(teacher, code)


def disable(teacher) -> None:
    """Turn 2FA off + wipe the secret + backup codes.

    Callers MUST verify a current second factor first (a TOTP or an unused
    backup code): this wipes the secret, and `two_factor_setup` then hands out a
    fresh one to anyone who reaches it, so an unauthenticated disable is a full
    second-factor takeover. `two_factor_manage` enforces this before calling —
    do not add another caller that skips it."""
    teacher.two_factor_secret = ""
    teacher.two_factor_enabled = False
    teacher.two_factor_backup_codes = []
    teacher.save(
        update_fields=[
            "two_factor_secret",
            "two_factor_enabled",
            "two_factor_backup_codes",
            "updated_at",
        ]
    )


def rotate_backup_codes(teacher) -> list[str]:
    """Generate a fresh set of backup codes and store the hashes. Returns
    the plaintext codes so the caller can show them once."""
    codes = generate_backup_codes()
    teacher.two_factor_backup_codes = [_hash_code(c) for c in codes]
    teacher.save(update_fields=["two_factor_backup_codes", "updated_at"])
    return codes


__all__ = [
    "EnrolmentPayload",
    "begin_enrolment",
    "confirm_enrolment",
    "disable",
    "generate_backup_codes",
    "rotate_backup_codes",
    "verify_backup_code",
    "verify_code",
    "verify_totp",
]
