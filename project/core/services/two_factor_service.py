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
- Backup codes are stored HASHED (sha256). We generate 8 codes at enrolment
  and each is consumed on use. Losing the phone + all backup codes = admin
  password + `manage.py reset_two_factor <email>` recovery.
- Only admins (`Teacher.admin=True`) are prompted for 2FA at login. Non-
  admin teachers don't have access to sensitive endpoints anyway.
"""

from __future__ import annotations

import base64
import hashlib
import io
import secrets
from dataclasses import dataclass

import pyotp
import qrcode
from django.conf import settings

_ISSUER = "Five a Day"
_BACKUP_CODE_COUNT = 8


@dataclass(frozen=True)
class EnrolmentPayload:
    """Everything the setup page needs to render the QR + confirmation form."""

    secret: str
    provisioning_uri: str
    qr_png_base64: str
    backup_codes: list[str]  # plaintext, shown ONCE at enrolment


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def _issuer_name() -> str:
    """Include the environment in the issuer so a dev + prod entry in the
    authenticator app don't collide on the phone."""
    env = getattr(settings, "ENVIRONMENT", "development")
    if env == "production":
        return _ISSUER
    return f"{_ISSUER} ({env})"


def generate_backup_codes(n: int = _BACKUP_CODE_COUNT) -> list[str]:
    """Generate `n` human-readable single-use codes (8 hex chars uppercase).

    They're returned in plaintext so the caller can show them to the user
    exactly once. The caller is responsible for persisting them via
    `set_backup_codes` (which stores only the sha256 hashes).
    """
    return [secrets.token_hex(4).upper() for _ in range(n)]


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
    """Return True iff `code` is a valid TOTP for this teacher.

    Accepts the current 30-second window plus one before/after (pyotp
    `valid_window=1`) so a slightly-off clock still works.
    """
    if not teacher.two_factor_secret:
        return False
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False
    totp = pyotp.TOTP(teacher.two_factor_secret)
    return totp.verify(code, valid_window=1)


def verify_backup_code(teacher, code: str) -> bool:
    """
    Verify a one-time backup code. On success, REMOVES the used code from
    the hashed list (backup codes are single-use).
    """
    code = (code or "").strip()
    if not code:
        return False
    hashed = _hash_code(code)
    codes = list(teacher.two_factor_backup_codes or [])
    if hashed not in codes:
        return False
    codes.remove(hashed)
    teacher.two_factor_backup_codes = codes
    teacher.save(update_fields=["two_factor_backup_codes", "updated_at"])
    return True


def verify_code(teacher, code: str) -> bool:
    """Try TOTP first, fall back to backup code. Used by the login gate."""
    return verify_totp(teacher, code) or verify_backup_code(teacher, code)


def disable(teacher) -> None:
    """Turn 2FA off + wipe the secret + backup codes. Callers must be sure
    the user has authenticated with the second factor at least once during
    this session (the view enforces that guard)."""
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
