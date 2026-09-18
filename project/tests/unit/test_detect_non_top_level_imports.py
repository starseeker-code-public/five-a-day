"""Tests for `scripts/detect_non_top_level_imports.py`.

The script is the ONE definition of "non-top-level import" for this repo — the
`update-readme` skill reports from it, and CLAUDE.md's rule points at it. Same
shape as `test_detect_destructive_migrations.py` next door, and the same reason
for existing: an inline copy of the rule in each caller is what drifts.

`scripts/` is excluded from the runtime image by `.dockerignore` but bind-mounted
by the dev compose override, so this runs under `make test` and in CI.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "detect_non_top_level_imports.py"


def _load():
    spec = importlib.util.spec_from_file_location("detect_non_top_level_imports", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


detector = _load()


def _kinds(source: str) -> list[str]:
    return [hit[1] for hit in detector.find_hits_in_source(source, "<test>")]


class TestClassification:
    def test_a_plain_top_level_import_is_not_reported(self):
        assert detector.find_hits_in_source("import os\nfrom pathlib import Path\n", "<test>") == []

    def test_a_function_body_import_is_deferred(self):
        src = "def f():\n    import os\n    return os\n"
        assert _kinds(src) == ["deferred"]

    def test_a_method_body_import_is_deferred(self):
        src = "class C:\n    def m(self):\n        import os\n        return os\n"
        assert _kinds(src) == ["deferred"]

    def test_try_except_importerror_is_an_import_guard(self):
        src = "def f():\n    try:\n        import twilio\n    except ImportError:\n        twilio = None\n    return twilio\n"
        assert _kinds(src) == ["import-guard"]

    def test_modulenotfounderror_counts_as_an_import_guard(self):
        src = "def f():\n    try:\n        import x\n    except ModuleNotFoundError:\n        x = None\n    return x\n"
        assert _kinds(src) == ["import-guard"]

    def test_type_checking_block_is_named_as_such(self):
        src = "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from x import Y\n"
        assert _kinds(src) == ["type-checking"]

    def test_sole_statement_of_a_non_import_guard_block(self):
        """The import IS the conditional — hoisting it would empty the block."""
        src = "def f():\n    try:\n        from x import Y\n    except Exception:\n        Y = ()\n    return Y\n"
        assert _kinds(src) == ["sole-statement"]

    def test_a_try_that_is_error_handling_not_an_import_guard_is_deferred(self):
        """A `try` around business logic is not a reason to defer an import."""
        src = "def f():\n    try:\n        from x import Y\n        return Y()\n    except ValueError:\n        return None\n"
        assert _kinds(src) == ["deferred"]

    def test_module_level_import_inside_a_plain_if_is_reported(self):
        src = "import sys\n\nif sys.version_info >= (3, 11):\n    import tomllib\n    import os\n"
        assert _kinds(src) == ["deferred", "deferred"]


class TestReporting:
    def test_hits_carry_line_kind_context_and_source(self):
        src = "def outer():\n    def inner():\n        from a import b\n        return b\n    return inner\n"
        ((line, kind, context, source),) = detector.find_hits_in_source(src, "<test>")
        assert line == 3
        assert kind == "deferred"
        assert context == "outer() > inner()"
        assert source == "from a import b"

    def test_a_module_level_hit_names_the_block_as_its_location(self):
        """`in import-guard` read like a function name; it is a location."""
        src = "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from x import Y\n"
        ((_line, _kind, context, _source),) = detector.find_hits_in_source(src, "<test>")
        assert context == "module level (type-checking)"

    def test_a_multi_line_import_is_reported_on_one_line(self):
        src = "def f():\n    from a import (\n        b,\n        c,\n    )\n    return b, c\n"
        ((_line, _kind, _context, source),) = detector.find_hits_in_source(src, "<test>")
        assert "from a import (" in source and "c," in source

    def test_files_format_prints_nothing_when_clean(self):
        """Shells test this with `[ -n "$X" ]` — a 'nothing found' line here
        would mark every clean release as having deferred imports."""
        assert detector.render({}, "files") == ""

    def test_text_format_says_so_when_clean(self):
        assert "No non-top-level imports" in detector.render({}, "text")

    def test_markdown_is_empty_when_clean_and_a_table_otherwise(self):
        assert detector.render({}, "markdown") == ""
        found = {"a.py": [(3, "deferred", "f()", "import os")]}
        out = detector.render(found, "markdown")
        assert "| `a.py` | 3 | deferred | `import os` |" in out

    def test_an_unknown_format_is_a_detector_error(self):
        with pytest.raises(detector.DetectorError):
            detector.render({}, "nope")


class TestExitStatus:
    """0 clean / 1 found / 2 the detector itself broke — every caller must treat
    2 as 'assume the worst', because 'printed nothing' and 'found nothing' are
    otherwise the same observation."""

    def test_clean_file_exits_zero(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text("import os\n", encoding="utf-8")
        assert detector.main([str(f), "--quiet"]) == 0

    def test_dirty_file_exits_one(self, tmp_path):
        f = tmp_path / "dirty.py"
        f.write_text("def f():\n    import os\n    return os\n", encoding="utf-8")
        assert detector.main([str(f), "--quiet"]) == 1

    def test_unreadable_file_exits_two(self, tmp_path):
        assert detector.main([str(tmp_path / "missing.py"), "--quiet"]) == 2

    def test_unparseable_file_exits_two(self, tmp_path):
        f = tmp_path / "broken.py"
        f.write_text("def f(:\n", encoding="utf-8")
        assert detector.main([str(f), "--quiet"]) == 2

    def test_no_paths_at_all_exits_zero(self):
        assert detector.main(["--quiet"]) == 0

    def test_deferred_only_filters_out_the_justified_shapes(self, tmp_path):
        f = tmp_path / "guarded.py"
        f.write_text(
            "def f():\n    try:\n        import twilio\n    except ImportError:\n        twilio = None\n    return twilio\n",
            encoding="utf-8",
        )
        assert detector.main([str(f), "--quiet"]) == 1
        assert detector.main([str(f), "--deferred-only", "--quiet"]) == 0
