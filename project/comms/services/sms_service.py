"""
SMS notification service (v1.8).

Wraps the Twilio SDK behind a `SmsService` that:
  - Reports itself as unconfigured when creds are missing (no crash on import).
  - Delegates to Twilio only if `twilio` is installed AND the settings block is
    populated. Otherwise `send()` returns a structured failure so callers can
    fall back to email without special-casing ImportError.
  - Ignores parents with `sms_opt_in=False` — the caller is expected to filter
    the queryset, but `send_to_parent()` guards against accidental opt-outs.

The Twilio dependency is intentionally *not* pinned in pyproject.toml — the
service imports it lazily so environments that don't need SMS never pay the
install cost. When it's needed, run `uv add twilio` before enabling the
setting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings

from comms.log_safe import safe_log

logger = logging.getLogger(__name__)


@dataclass
class SmsResult:
    success: bool
    to: str = ""
    message_sid: str = ""
    error: str = ""

    def as_dict(self):
        return {
            "success": self.success,
            "to": self.to,
            "message_sid": self.message_sid,
            "error": self.error,
        }


class SmsService:
    def __init__(self):
        self.account_sid = getattr(settings, "TWILIO_ACCOUNT_SID", "")
        self.auth_token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
        self.from_number = getattr(settings, "TWILIO_FROM_NUMBER", "")
        self._client = None

    def is_configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.from_number)

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from twilio.rest import Client  # noqa: PLC0415 — lazy import (optional dep)
        except ImportError as e:
            raise RuntimeError("twilio package is not installed — run `uv add twilio`") from e
        self._client = Client(self.account_sid, self.auth_token)
        return self._client

    def send(self, to: str, body: str) -> SmsResult:
        """Low-level send — no opt-in check. Callers must verify consent first."""
        if not self.is_configured():
            return SmsResult(success=False, to=to, error="SMS service not configured")
        try:
            client = self._get_client()
            msg = client.messages.create(body=body, from_=self.from_number, to=to)
            return SmsResult(success=True, to=to, message_sid=getattr(msg, "sid", ""))
        except Exception as e:  # noqa: BLE001 — network / API failure surfaces as result
            # `to` is caller-supplied (a Parent.phone, ultimately typed in by an
            # admin) and the Twilio error text is remote input, so neither is
            # safe to interpolate raw. `SmsResult.error` gets the same treatment
            # because callers surface it in responses.
            logger.warning("SMS send failed to %s: %s", safe_log(to), safe_log(e))
            return SmsResult(success=False, to=to, error=safe_log(e))

    def send_to_parent(self, parent, body: str) -> SmsResult:
        """
        Guarded send that respects `parent.sms_opt_in` and the presence of a
        phone number. Returns success=False with an explanatory error for
        skipped sends so callers can distinguish opt-out from delivery failure.
        """
        if not getattr(parent, "sms_opt_in", False):
            return SmsResult(success=False, to=parent.phone or "", error="parent has not opted in")
        if not parent.phone:
            return SmsResult(success=False, error="parent has no phone number")
        return self.send(parent.phone, body)


def get_sms_service() -> SmsService:
    return SmsService()


__all__ = ["SmsResult", "SmsService", "get_sms_service"]
