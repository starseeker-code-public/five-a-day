"""Tests for `core.log_safe.safe_log` — the log-injection sanitizer (v1.14.4)."""

import pytest

from core.log_safe import safe_log


class TestSafeLog:
    @pytest.mark.parametrize(
        "raw",
        [
            "user\r\nINFO Fake log line",
            "user\nINFO Fake log line",
            "user\rINFO Fake log line",
            "user\vINFO Fake log line",
            "user\fINFO Fake log line",
        ],
    )
    def test_line_breaks_are_replaced(self, raw):
        """No forged record can be smuggled in: nothing that a log writer
        emits as a line break survives."""
        cleaned = safe_log(raw)
        assert "\r" not in cleaned
        assert "\n" not in cleaned
        assert "\v" not in cleaned
        assert "\f" not in cleaned
        assert len(cleaned.splitlines()) == 1

    def test_escape_char_is_stripped(self):
        """ESC would let an attacker inject terminal colour/clear sequences
        into a tailed log file."""
        assert "\x1b" not in safe_log("boom\x1b[2J")

    def test_plain_value_is_unchanged(self):
        assert safe_log("parent@example.com") == "parent@example.com"

    def test_non_string_is_coerced(self):
        assert safe_log(42) == "42"
        assert safe_log(None) == "None"

    def test_long_value_is_truncated_with_ellipsis(self):
        cleaned = safe_log("x" * 500)
        assert cleaned.endswith("...")
        assert len(cleaned) == 203  # 200 chars + the "..." marker

    def test_max_len_is_overridable(self):
        assert safe_log("abcdef", max_len=3) == "abc..."

    def test_value_at_the_limit_is_not_truncated(self):
        cleaned = safe_log("x" * 200)
        assert cleaned == "x" * 200
        assert not cleaned.endswith("...")


class TestCommsSafeLogParity:
    """`comms.log_safe.safe_log` is a deliberate byte-for-byte copy of
    `core.log_safe.safe_log` (comms must not import core), but it is the copy
    on the LIVE email/SMS error paths — so it must behave identically. The core
    copy was the only one tested; this pins the comms one too."""

    @pytest.mark.parametrize(
        "raw,expected_contains_no",
        [
            ("user\r\nINFO forged", "\n"),
            ("user\rINFO forged", "\r"),
        ],
    )
    def test_line_breaks_are_replaced(self, raw, expected_contains_no):
        from comms.log_safe import safe_log as comms_safe_log

        cleaned = comms_safe_log(raw)
        assert expected_contains_no not in cleaned

    def test_matches_core_implementation(self):
        from comms.log_safe import safe_log as comms_safe_log

        for value in ["plain@x.com", 42, None, "a\r\nb", "x" * 500, "x" * 200]:
            assert comms_safe_log(value) == safe_log(value)
