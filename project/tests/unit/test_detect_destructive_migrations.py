"""Tests for `scripts/detect_destructive_migrations.py`.

That script is the SINGLE definition of "destructive" for five surfaces: the
production preflight (Gate 3), the production deploy gate that enforces
`ack_destructive`, the auto-merge release PR body and email, the testing-deploy
email, and the `update-readme` skill's report. A miss here is silent in the
worst possible direction — a release that drops schema sails through every
advisory surface AND the enforcing gate, because "no output" and "nothing
found" are the same thing to every caller.

`scripts/` is excluded from the runtime image by `.dockerignore`, but the dev
compose override bind-mounts the repo at `/app`, so both `make test` and CI
(which runs pytest straight on the runner) can import it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "detect_destructive_migrations.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("detect_destructive_migrations", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


detector = _load()


def _migration(tmp_path: Path, body: str, name: str = "0001_test.py") -> Path:
    path = tmp_path / name
    path.write_text(
        "from django.db import migrations\n\n\n"
        "class Migration(migrations.Migration):\n"
        f"    operations = [\n{body}\n    ]\n",
        encoding="utf-8",
    )
    return path


class TestDetection:
    @pytest.mark.parametrize(
        "operation",
        [
            'migrations.RemoveField(model_name="x", name="y"),',
            'migrations.DeleteModel(name="X"),',
            'migrations.RenameField(model_name="x", old_name="a", new_name="b"),',
            'migrations.RenameModel(old_name="A", new_name="B"),',
        ],
    )
    def test_django_destructive_operations_are_flagged(self, tmp_path: Path, operation: str) -> None:
        path = _migration(tmp_path, f"        {operation}")
        assert detector.find_hits([str(path)])

    @pytest.mark.parametrize(
        "sql",
        [
            'migrations.RunSQL("DROP TABLE students;"),',
            'migrations.RunSQL("drop table students;"),',
            'migrations.RunSQL("Drop   Column foo;"),',
            'migrations.RunSQL("TRUNCATE payments;"),',
            'migrations.RunSQL("truncate payments;"),',
        ],
    )
    def test_raw_sql_is_matched_case_insensitively(self, tmp_path: Path, sql: str) -> None:
        """The shell original was case-sensitive throughout.

        A `RunSQL("drop table ...")` therefore walked straight past the
        production gate. Widening this can only flag MORE releases as
        destructive, which is the safe direction to be wrong in.
        """
        path = _migration(tmp_path, f"        {sql}")
        assert detector.find_hits([str(path)])

    @pytest.mark.parametrize(
        "operation",
        [
            'migrations.AddField(model_name="x", name="y", field=None),',
            'migrations.CreateModel(name="X", fields=[]),',
            'migrations.AlterField(model_name="x", name="y", field=None),',
            'migrations.AddIndex(model_name="x", index=None),',
            'migrations.RunSQL("UPDATE payments SET amount = 0;"),',
        ],
    )
    def test_additive_operations_are_not_flagged(self, tmp_path: Path, operation: str) -> None:
        path = _migration(tmp_path, f"        {operation}")
        assert detector.find_hits([str(path)]) == {}

    def test_missing_paths_are_skipped_not_fatal(self, tmp_path: Path) -> None:
        """`git diff --name-only` lists DELETED files too.

        A migration removed by the range under inspection cannot be applied by
        it, so its absence is not an error.
        """
        assert detector.find_hits([str(tmp_path / "gone.py")]) == {}

    def test_hits_carry_line_numbers(self, tmp_path: Path) -> None:
        path = _migration(tmp_path, '        migrations.RemoveField(model_name="x", name="y"),')
        hits = detector.find_hits([str(path)])
        ((line_no, text),) = hits[str(path)]
        assert line_no > 0
        assert "RemoveField" in text


class TestRendering:
    def test_every_format_is_empty_when_clean(self) -> None:
        """Empty is what every caller reads as 'nothing to say'.

        The workflows branch on `-n "$DESTRUCTIVE"`, and the PR body and emails
        splice the block in only when it is non-empty.
        """
        for fmt in ("text", "markdown", "html", "files"):
            assert detector.render({}, fmt) == ""

    def test_files_format_is_bare_paths(self, tmp_path: Path) -> None:
        path = _migration(tmp_path, '        migrations.RemoveField(model_name="x", name="y"),')
        hits = detector.find_hits([str(path)])
        assert detector.render(hits, "files") == str(path)

    def test_markdown_names_ack_destructive_and_warns_off_force(self, tmp_path: Path) -> None:
        path = _migration(tmp_path, '        migrations.DeleteModel(name="X"),')
        body = detector.render(detector.find_hits([str(path)]), "markdown")
        assert "ack_destructive" in body
        assert "force" in body

    def test_html_escapes_file_content(self, tmp_path: Path) -> None:
        """The rendered block goes into an HTML email verbatim.

        The line text comes out of a migration file, so it is repo content
        rather than a stranger's input — but it reaches an inbox unparsed, and
        `<` in a raw SQL comparison would silently eat the rest of the message.
        """
        path = _migration(tmp_path, '        migrations.RunSQL("DROP TABLE x WHERE a <b>c;"),')
        html = detector.render(detector.find_hits([str(path)]), "html")
        assert "<b>" not in html
        assert "&lt;b&gt;" in html

    def test_unknown_format_raises_rather_than_returning_empty(self) -> None:
        """An empty string means 'clean' to every caller — never a bad format."""
        with pytest.raises(detector.DetectorError):
            detector.render({"f": [(1, "RemoveField")]}, "yaml")


class TestExitStatus:
    """0 clean / 1 found / 2 error. Callers treat 2 as 'assume the worst'."""

    def test_clean_exits_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = _migration(tmp_path, '        migrations.AddField(model_name="x", name="y", field=None),')
        assert detector.main([str(path), "--format", "files"]) == 0

    def test_found_exits_one(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = _migration(tmp_path, '        migrations.RemoveField(model_name="x", name="y"),')
        assert detector.main([str(path), "--format", "files"]) == 1

    def test_no_arguments_is_clean_not_an_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert detector.main(["--format", "files"]) == 0

    def test_detector_error_exits_two(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def boom(*_args: object, **_kwargs: object) -> list[str]:
            raise detector.DetectorError("git exploded")

        monkeypatch.setattr(detector, "migrations_in_range", boom)
        assert detector.main(["--range", "a..b"]) == 2

    def test_files_format_prints_nothing_when_clean(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A shell captures this into a variable and tests it for emptiness.

        Printing a friendly "nothing found" line here would make every clean
        release look destructive to `[ -n "$DESTRUCTIVE" ]`.
        """
        path = _migration(tmp_path, '        migrations.AddField(model_name="x", name="y", field=None),')
        detector.main([str(path), "--format", "files"])
        assert capsys.readouterr().out.strip() == ""


class TestGithubOutput:
    def test_writes_flag_count_and_rendered_blocks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _migration(tmp_path, '        migrations.RemoveField(model_name="x", name="y"),')
        out = tmp_path / "gh_output"
        out.write_text("", encoding="utf-8")
        monkeypatch.setenv("GITHUB_OUTPUT", str(out))

        detector.main([str(path), "--github-output", "--quiet"])

        written = out.read_text(encoding="utf-8")
        assert "destructive=true" in written
        assert "destructive_count=1" in written
        assert "destructive_markdown<<" in written
        assert "destructive_html<<" in written

    def test_writes_false_when_clean(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _migration(tmp_path, '        migrations.AddField(model_name="x", name="y", field=None),')
        out = tmp_path / "gh_output"
        out.write_text("", encoding="utf-8")
        monkeypatch.setenv("GITHUB_OUTPUT", str(out))

        detector.main([str(path), "--github-output", "--quiet"])

        assert "destructive=false" in out.read_text(encoding="utf-8")

    def test_is_a_noop_outside_actions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
        path = _migration(tmp_path, '        migrations.RemoveField(model_name="x", name="y"),')
        assert detector.main([str(path), "--github-output", "--quiet"]) == 1

    def test_delimiter_is_run_unique(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A fixed delimiter in a multi-line output is forgeable.

        The blocks carry text read out of migration files, so a crafted line
        could close the heredoc early and inject extra step outputs.
        """
        path = _migration(tmp_path, '        migrations.RemoveField(model_name="x", name="y"),')
        seen = set()
        for name in ("a", "b"):
            out = tmp_path / name
            out.write_text("", encoding="utf-8")
            monkeypatch.setenv("GITHUB_OUTPUT", str(out))
            detector.main([str(path), "--github-output", "--quiet"])
            line = next(ln for ln in out.read_text(encoding="utf-8").splitlines() if "markdown<<" in ln)
            seen.add(line.split("<<", 1)[1])
        assert len(seen) == 2


class TestRealRepositoryMigrations:
    """Pins the behaviour against the migration that motivated all of this."""

    def test_billing_0017_is_detected_as_destructive(self) -> None:
        path = _REPO_ROOT / "project" / "billing" / "migrations" / "0017_drop_dead_pricing_columns.py"
        if not path.is_file():
            pytest.skip("billing/0017 not present in this checkout")
        assert detector.find_hits([str(path)])


class TestAppliedTense:
    """The same block is mailed before a release ships AND after it has.

    "El despliegue exigira ack_destructive" read on a deploy-SUCCEEDED email is
    confusing at exactly the moment somebody is deciding whether to worry, so
    the production success mail passes `--applied`.
    """

    def test_default_wording_is_forward_looking(self, tmp_path: Path) -> None:
        path = _migration(tmp_path, '        migrations.RemoveField(model_name="x", name="y"),')
        html = detector.render(detector.find_hits([str(path)]), "html")
        assert "exigira" in html
        assert "ya NO existen" not in html

    def test_applied_wording_is_past_tense_and_names_the_backup(self, tmp_path: Path) -> None:
        path = _migration(tmp_path, '        migrations.RemoveField(model_name="x", name="y"),')
        html = detector.render(detector.find_hits([str(path)]), "html", applied=True)
        assert "ya NO existen" in html
        assert "copia de seguridad" in html
        assert "exigira" not in html

    def test_applied_is_ignored_for_non_html_formats(self, tmp_path: Path) -> None:
        path = _migration(tmp_path, '        migrations.RemoveField(model_name="x", name="y"),')
        hits = detector.find_hits([str(path)])
        assert detector.render(hits, "files", applied=True) == detector.render(hits, "files")

    def test_github_output_carries_the_applied_wording(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _migration(tmp_path, '        migrations.RemoveField(model_name="x", name="y"),')
        out = tmp_path / "gh_output"
        out.write_text("", encoding="utf-8")
        monkeypatch.setenv("GITHUB_OUTPUT", str(out))

        detector.main([str(path), "--github-output", "--quiet", "--applied"])

        assert "ya NO existen" in out.read_text(encoding="utf-8")
