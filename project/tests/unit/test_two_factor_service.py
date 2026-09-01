"""Unit tests for the TOTP two-factor service (v1.13)."""

from unittest.mock import patch

import pyotp
import pytest

from core.services import two_factor_service as tfs

pytestmark = pytest.mark.django_db


class TestBeginEnrolment:
    def test_generates_secret_qr_and_backup_codes(self, teacher):
        from urllib.parse import quote

        payload = tfs.begin_enrolment(teacher)
        teacher.refresh_from_db()
        assert payload.secret
        assert teacher.two_factor_secret == payload.secret
        assert payload.provisioning_uri.startswith("otpauth://totp/")
        # Email is URL-encoded inside the provisioning URI
        assert quote(teacher.email, safe="") in payload.provisioning_uri
        assert payload.qr_png_base64
        assert len(payload.backup_codes) == 8
        # 16 hex chars = 64 bits (was 8 chars / 32 bits before v1.23.0, which is
        # brute-forceable offline once the hashes leak).
        assert all(len(c) == 16 for c in payload.backup_codes)
        # Stored codes go through Django's password hasher, not a bare sha256:
        # they carry an `algorithm$...` prefix and never equal the plaintext.
        assert all("$" in h for h in teacher.two_factor_backup_codes)
        assert not set(payload.backup_codes) & set(teacher.two_factor_backup_codes)
        # Not enabled until confirmed
        assert teacher.two_factor_enabled is False


class TestConfirmEnrolment:
    def test_valid_code_flips_enabled(self, teacher):
        payload = tfs.begin_enrolment(teacher)
        code = pyotp.TOTP(payload.secret).now()
        assert tfs.confirm_enrolment(teacher, code) is True
        teacher.refresh_from_db()
        assert teacher.two_factor_enabled is True

    def test_wrong_code_rejected(self, teacher):
        tfs.begin_enrolment(teacher)
        assert tfs.confirm_enrolment(teacher, "000000") is False
        teacher.refresh_from_db()
        assert teacher.two_factor_enabled is False

    def test_no_secret_staged_rejects_any_code(self, teacher):
        teacher.two_factor_secret = ""
        teacher.save()
        assert tfs.confirm_enrolment(teacher, "123456") is False


class TestVerifyTotp:
    def test_valid_code_accepted(self, teacher):
        secret = pyotp.random_base32()
        teacher.two_factor_secret = secret
        teacher.save()
        code = pyotp.TOTP(secret).now()
        assert tfs.verify_totp(teacher, code) is True

    def test_whitespace_stripped(self, teacher):
        secret = pyotp.random_base32()
        teacher.two_factor_secret = secret
        teacher.save()
        code = pyotp.TOTP(secret).now()
        assert tfs.verify_totp(teacher, f" {code} ") is True

    def test_non_digit_rejected(self, teacher):
        teacher.two_factor_secret = pyotp.random_base32()
        teacher.save()
        assert tfs.verify_totp(teacher, "abcdef") is False

    def test_no_secret_rejects(self, teacher):
        assert tfs.verify_totp(teacher, "123456") is False


class TestVerifyBackupCode:
    def test_valid_backup_code_consumed(self, teacher):
        payload = tfs.begin_enrolment(teacher)
        original_hashes = list(teacher.two_factor_backup_codes)
        code = payload.backup_codes[0]
        assert tfs.verify_backup_code(teacher, code) is True
        teacher.refresh_from_db()
        assert len(teacher.two_factor_backup_codes) == len(original_hashes) - 1

    def test_used_backup_code_rejected_on_replay(self, teacher):
        payload = tfs.begin_enrolment(teacher)
        code = payload.backup_codes[0]
        tfs.verify_backup_code(teacher, code)
        assert tfs.verify_backup_code(teacher, code) is False

    def test_unknown_code_rejected(self, teacher):
        tfs.begin_enrolment(teacher)
        assert tfs.verify_backup_code(teacher, "DEADBEEF") is False

    def test_empty_code_rejected(self, teacher):
        assert tfs.verify_backup_code(teacher, "") is False


class TestVerifyCode:
    def test_totp_then_backup(self, teacher):
        """`verify_code` tries TOTP first; falls back to backup on TOTP miss."""
        payload = tfs.begin_enrolment(teacher)
        backup = payload.backup_codes[0]
        # Backup code is verified even though it looks nothing like a TOTP
        assert tfs.verify_code(teacher, backup) is True


class TestDisable:
    def test_wipes_state(self, teacher):
        tfs.begin_enrolment(teacher)
        tfs.disable(teacher)
        teacher.refresh_from_db()
        assert teacher.two_factor_secret == ""
        assert teacher.two_factor_enabled is False
        assert teacher.two_factor_backup_codes == []


class TestRotateBackupCodes:
    def test_returns_new_plaintext_codes(self, teacher):
        tfs.begin_enrolment(teacher)
        old_hashes = list(teacher.two_factor_backup_codes)
        new_codes = tfs.rotate_backup_codes(teacher)
        teacher.refresh_from_db()
        assert len(new_codes) == 8
        # Stored hashes changed
        assert teacher.two_factor_backup_codes != old_hashes
        # Old codes no longer valid
        old_plaintext_would_be = "OLDCODE1"  # never generated → never valid
        assert tfs.verify_backup_code(teacher, old_plaintext_would_be) is False
        # New codes valid
        assert tfs.verify_backup_code(teacher, new_codes[0]) is True


class TestIssuerName:
    def test_dev_env_marks_issuer(self):
        with patch("core.services.two_factor_service.settings") as mock_settings:
            mock_settings.ENVIRONMENT = "development"
            assert "(development)" in tfs._issuer_name()

    def test_production_is_clean(self):
        with patch("core.services.two_factor_service.settings") as mock_settings:
            mock_settings.ENVIRONMENT = "production"
            assert tfs._issuer_name() == "Five a Day"
