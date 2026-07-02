"""
Parent portal — magic-link session token (v1.9).

Kept in its own module rather than folded into `students.models` so the
top-level student data model stays focused. Imported from `students.models`
so Django's app-registry picks it up.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone


def _fresh_token() -> str:
    """32 hex chars — 128 bits of entropy, matches Django's default token widths."""
    return secrets.token_hex(16)


class ParentSessionToken(models.Model):
    """
    Single-use magic-link token. Emailed to the parent; consumed on the
    /parent/login/<token>/ endpoint, which sets the session and marks the
    token as used. Expires 30 minutes after creation.
    """

    parent = models.ForeignKey("students.Parent", on_delete=models.CASCADE, related_name="session_tokens")
    token = models.CharField(max_length=64, unique=True, default=_fresh_token)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "parent_session_tokens"
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"{self.parent} (expires {self.expires_at:%Y-%m-%d %H:%M})"

    @classmethod
    def issue(cls, parent, ttl_minutes: int = 30) -> ParentSessionToken:
        return cls.objects.create(
            parent=parent,
            expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
        )

    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()

    def consume(self) -> bool:
        """Non-atomic consume — kept for backwards compatibility. Callers
        vulnerable to concurrent requests on the same token should use
        `consume_by_token` instead, which wraps the read-check-write in a
        SELECT FOR UPDATE."""
        if not self.is_valid():
            return False
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])
        return True

    @classmethod
    def consume_by_token(cls, token: str) -> ParentSessionToken | None:
        """
        Atomically look up + consume a token in one transaction.

        Two concurrent GETs on the same magic link previously hit a TOCTOU
        window: both `is_valid()` calls saw `used_at=None` before either
        wrote back, so both consumed the token and both got a valid session.
        `select_for_update()` inside `transaction.atomic()` serialises the
        check-and-consume so exactly one of the racing requests wins.

        Returns the freshly consumed token, or None when the token is
        unknown / expired / already used.
        """
        with transaction.atomic():
            try:
                token_row = cls.objects.select_for_update().select_related("parent").get(token=token)
            except cls.DoesNotExist:
                return None
            if token_row.used_at is not None or token_row.expires_at <= timezone.now():
                return None
            token_row.used_at = timezone.now()
            token_row.save(update_fields=["used_at"])
            return token_row
