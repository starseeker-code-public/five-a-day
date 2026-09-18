"""Tests for `core.logging_utils` — correlation ids and the Cloud Logging format.

Why this module is worth pinning: it is the layer that decides whether a
production incident is READABLE. The bug it was written for (a welcome email
that failed in production and produced three alert mails, none of which could
say why, and none of which could be tied to the others) is invisible to every
functional test in the suite — the app behaved exactly as designed.
"""

import json
import logging
import sys

import pytest

from core.logging_utils import (
    CloudLoggingFormatter,
    HumanFormatter,
    RequestContextFilter,
    get_request_id,
    get_trace_id,
    new_request_id,
    reset_request_id,
    reset_trace_id,
    sanitize_request_id,
    sanitize_trace_id,
    set_request_id,
    set_trace_id,
)


def make_record(level=logging.INFO, msg="hello %s", args=("world",), exc_info=None, **extra):
    record = logging.LogRecord(
        name="core.views.payments",
        level=level,
        pathname="/app/project/core/views/payments.py",
        lineno=42,
        msg=msg,
        args=args,
        exc_info=exc_info,
        func="create_payment",
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestRequestId:
    def test_absent_outside_a_request(self):
        assert get_request_id() == ""

    def test_bind_and_reset_round_trip(self):
        token = set_request_id("abc123")
        try:
            assert get_request_id() == "abc123"
        finally:
            reset_request_id(token)
        # The reset matters more than the bind: Gunicorn reuses threads, so an
        # id left behind files the NEXT request's failure under this id.
        assert get_request_id() == ""

    def test_new_ids_are_unique_and_short(self):
        first, second = new_request_id(), new_request_id()
        assert first != second
        assert len(first) == 16

    @pytest.mark.parametrize(
        "hostile",
        [
            "abc\nINFO forged record",
            "abc\r\nERROR forged record",
            "x" * 65,
            "id with spaces",
            "",
            None,
        ],
    )
    def test_hostile_inbound_ids_are_replaced_not_cleaned(self, hostile):
        """An `X-Request-ID` is client-controlled. A newline in it forges log
        records, so anything unrecognised is swapped for a fresh id rather than
        scrubbed — a half-cleaned id is no longer the id the client quoted."""
        result = sanitize_request_id(hostile)
        assert result != hostile
        assert len(result) == 16

    @pytest.mark.parametrize("ok", ["abc123", "a-b_c.d:e", "0123456789abcdef"])
    def test_wellformed_ids_survive(self, ok):
        assert sanitize_request_id(ok) == ok


class TestTraceId:
    """Cloud Run's own trace id — the thing that nests our entries under ITS
    request log. Kept separate from the request id because it can only come
    from Google's front end."""

    def test_absent_by_default(self):
        assert get_trace_id() == ""

    @pytest.mark.parametrize("valid", ["105445aa7843bc8b", "ABCDEF0123456789abcdef0123456789", "a"])
    def test_hex_survives(self, valid):
        assert sanitize_trace_id(valid) == valid

    @pytest.mark.parametrize(
        "invalid",
        [
            "not-hex-at-all",
            "0123456789abcdef0123456789abcdef0",  # 33 chars
            "abc/def",
            "abc\ninjected",
            "",
            None,
        ],
    )
    def test_anything_else_becomes_empty_never_invented(self, invalid):
        """There is no minting a replacement: a made-up trace would file our
        records under a request that belongs to somebody else, which is worse
        than not grouping them at all."""
        assert sanitize_trace_id(invalid) == ""

    def test_filter_stamps_it(self):
        record = make_record()
        token = set_trace_id("105445aa7843bc8b")
        try:
            RequestContextFilter().filter(record)
        finally:
            reset_trace_id(token)
        assert record.trace_id == "105445aa7843bc8b"

    def test_formatter_and_filter_work_together(self):
        """The formatter reads `trace_id` off the record — a field NOTHING set
        until this was plumbed through, so the trace could never be emitted no
        matter how the project was configured."""
        record = make_record()
        token = set_trace_id("105445aa7843bc8b")
        try:
            RequestContextFilter().filter(record)
            payload = json.loads(CloudLoggingFormatter(project_id="five-a-day").format(record))
        finally:
            reset_trace_id(token)
        assert payload["logging.googleapis.com/trace"] == "projects/five-a-day/traces/105445aa7843bc8b"


class TestRequestContextFilter:
    def test_stamps_the_current_id(self):
        record = make_record()
        token = set_request_id("deadbeef")
        try:
            assert RequestContextFilter().filter(record) is True
        finally:
            reset_request_id(token)
        assert record.request_id == "deadbeef"

    def test_placeholder_when_there_is_no_request(self):
        record = make_record()
        RequestContextFilter().filter(record)
        assert record.request_id == "-"

    def test_never_drops_a_record(self):
        """A filter returning False silently deletes the log line. This one
        exists to ADD a field, so it must always pass the record through."""
        assert RequestContextFilter().filter(make_record(level=logging.ERROR)) is True


class TestCloudLoggingFormatter:
    def test_emits_severity_cloud_logging_understands(self):
        """The whole point: on Cloud Run a plain-text line takes its severity
        from the STREAM, so every INFO written to stderr showed up as an error
        and the console's severity filter selected everything."""
        payload = json.loads(CloudLoggingFormatter().format(make_record(level=logging.WARNING)))
        assert payload["severity"] == "WARNING"
        assert payload["message"] == "hello world"

    @pytest.mark.parametrize(
        ("level", "severity"),
        [
            (logging.DEBUG, "DEBUG"),
            (logging.INFO, "INFO"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
            (logging.CRITICAL, "CRITICAL"),
        ],
    )
    def test_every_level_maps(self, level, severity):
        payload = json.loads(CloudLoggingFormatter().format(make_record(level=level)))
        assert payload["severity"] == severity

    def test_unknown_level_degrades_instead_of_raising(self):
        payload = json.loads(CloudLoggingFormatter().format(make_record(level=57)))
        assert payload["severity"] == "DEFAULT"

    def test_source_location_is_filled(self):
        payload = json.loads(CloudLoggingFormatter().format(make_record()))
        assert payload["logger"] == "core.views.payments"
        assert payload["line"] == 42
        assert payload["logging.googleapis.com/sourceLocation"]["function"] == "create_payment"

    def test_traceback_is_inside_message(self):
        """A traceback that lives only in a collapsed field is a traceback
        nobody reads during an incident — the Logs Explorer preview row shows
        `message` and nothing else."""
        try:
            raise ValueError("boom")
        except ValueError:
            record = make_record(level=logging.ERROR, exc_info=sys.exc_info())
        payload = json.loads(CloudLoggingFormatter().format(record))
        assert "ValueError: boom" in payload["message"]
        assert "Traceback" in payload["message"]

    def test_request_id_travels(self):
        payload = json.loads(CloudLoggingFormatter().format(make_record(request_id="cafe1234")))
        assert payload["request_id"] == "cafe1234"

    def test_placeholder_id_is_omitted(self):
        payload = json.loads(CloudLoggingFormatter().format(make_record(request_id="-")))
        assert "request_id" not in payload

    def test_extra_fields_become_searchable_keys(self):
        payload = json.loads(CloudLoggingFormatter().format(make_record(status=500, url_name="create_payment")))
        assert payload["status"] == 500
        assert payload["url_name"] == "create_payment"

    def test_trace_needs_the_project_id(self):
        """Cloud Logging silently drops a bare trace id: it only links an entry
        to its request when the value is the fully-qualified resource name."""
        without = json.loads(CloudLoggingFormatter(project_id="").format(make_record(trace_id="abc")))
        assert "logging.googleapis.com/trace" not in without

        with_id = json.loads(CloudLoggingFormatter(project_id="five-a-day").format(make_record(trace_id="abc")))
        assert with_id["logging.googleapis.com/trace"] == "projects/five-a-day/traces/abc"

    def test_unserialisable_extra_does_not_take_the_record_with_it(self):
        """A formatter that raises loses the line AND makes the handler print
        its own error. An awkward `extra=` must degrade, never explode."""

        class Awkward:
            def __repr__(self):
                return "<awkward>"

        payload = json.loads(CloudLoggingFormatter().format(make_record(thing=Awkward())))
        assert payload["thing"] == "<awkward>"
        assert payload["message"] == "hello world"

    def test_one_line_per_record(self):
        """Cloud Logging splits on newlines, so a multi-line record would be
        ingested as several entries and lose its severity on all but the first."""
        rendered = CloudLoggingFormatter().format(make_record(msg="a\nb", args=()))
        assert len(rendered.splitlines()) == 1


class TestHumanFormatter:
    def test_prefixes_the_id_when_there_is_one(self):
        formatter = HumanFormatter("{levelname} {name}:{lineno} {message}", style="{")
        assert formatter.format(make_record(request_id="abc123")).startswith("[abc123] ")

    def test_no_dash_column_outside_a_request(self):
        formatter = HumanFormatter("{levelname} {name}:{lineno} {message}", style="{")
        rendered = formatter.format(make_record(request_id="-"))
        assert not rendered.startswith("[")
        assert "core.views.payments:42" in rendered
