#!/usr/bin/env python3
"""Detect imports that are not at module top level.

THE single definition of "non-top-level import" for this repository, so the
question is not answered differently in two places - the same reasoning as
``detect_destructive_migrations.py`` next door.

A function-body import is occasionally load-bearing and usually an accident.
The accident is expensive in a specific, silent way: an import bound inside a
function is invisible to the module's import block, so the dependency it
creates does not show up when anyone reads the file, and `mock.patch` aimed at
the DEFINING module keeps working - which makes a test that has quietly stopped
patching anything look green. A repo-wide pass in v1.29.8 found 859 of them,
converted 852, and 35 tests failed for exactly that reason.

So the default is: put it at the top. The exceptions are real but few, and each
one needs a written reason AT THE IMPORT - see ``--format text`` output, which
names the shape it matched so a reviewer can check the reason against it:

  * ``import-guard``   - inside ``try/except ImportError``: an optional or
    undeclared dependency, where a top-level import turns a degraded feature
    into an app that cannot start.
  * ``type-checking``  - inside ``if TYPE_CHECKING``: annotation-only by design.
  * ``sole-statement`` - the import IS the conditional; hoisting it would leave
    an empty block (typically a ``try`` whose ``except`` binds a fallback).
  * ``deferred``       - everything else. Nearly always the accident; this is
    the category a human has to rule on.

Stdlib only, like ``check_version_coherence.py``: it runs on a bare GitHub
runner and in a repo checkout with no virtualenv.

Exit status is a REPORT, not a verdict - the caller owns the policy:

    0  no non-top-level imports found
    1  non-top-level imports found
    2  usage / internal error (callers must treat this as "assume the worst")

Note 2 matters: "printed nothing" and "found nothing" are otherwise the same
observation, and a detector that fails open silently is worse than no detector.

Usage::

    detect_non_top_level_imports.py FILE [FILE ...]
    detect_non_top_level_imports.py --staged
    detect_non_top_level_imports.py --range origin/main..origin/testing

plus ``--format {text,markdown,files}``, ``--deferred-only`` and ``--quiet``.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

PY_GLOB = "*.py"


class DetectorError(RuntimeError):
    """The detector itself could not answer. Callers must assume the worst."""


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

#: (line, kind, context, source) for one offending import.
Hit = tuple[int, str, str, str]


def _statement_source(lines: list[str], node: ast.stmt) -> str:
    return " ".join(lines[node.lineno - 1 : node.end_lineno]).strip()


def _classify(guards: list[str], siblings: int) -> str:
    """Name the SHAPE the import sits in, never whether it is justified.

    Only a human can decide the latter, and the whole point of this script is to
    put the decision in front of one.
    """
    if "import-guard" in guards:
        return "import-guard"
    if "type-checking" in guards:
        return "type-checking"
    if siblings == 1 and guards:
        return "sole-statement"
    return "deferred"


def find_hits_in_source(source: str, path: str) -> list[Hit]:
    """Every import in `source` that is not a plain module-level statement."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise DetectorError(f"{path}: could not parse: {exc}") from exc

    lines = source.splitlines()
    hits: list[Hit] = []

    def walk(node: ast.AST, funcs: list[str], guards: list[str]) -> None:
        for _field, value in ast.iter_fields(node):
            if not isinstance(value, list):
                continue
            statements = [item for item in value if isinstance(item, ast.stmt)]
            for child in value:
                if not isinstance(child, ast.AST):
                    continue
                if isinstance(child, ast.Import | ast.ImportFrom):
                    # A plain module-level import: nothing to report.
                    if not funcs and not guards:
                        continue
                    kind = _classify(guards, len(statements))
                    context = " > ".join(funcs) if funcs else f"module level ({' > '.join(guards)})"
                    hits.append((child.lineno, kind, context, _statement_source(lines, child)))
                    continue
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    walk(child, [*funcs, f"{child.name}()"], guards)
                elif isinstance(child, ast.ClassDef):
                    walk(child, funcs, [*guards, f"class {child.name}"])
                elif isinstance(child, ast.If):
                    test = ast.dump(child.test)
                    label = "type-checking" if "TYPE_CHECKING" in test else "if"
                    walk(child, funcs, [*guards, label])
                elif isinstance(child, ast.Try) or type(child).__name__ == "TryStar":
                    handlers = " ".join(ast.dump(h) for h in child.handlers)
                    guard = "ImportError" in handlers or "ModuleNotFoundError" in handlers
                    walk(child, funcs, [*guards, "import-guard" if guard else "try"])
                else:
                    walk(child, funcs, guards)

    walk(tree, [], [])
    return sorted(hits)


def find_hits(paths: list[str], *, staged: bool = False) -> dict[str, list[Hit]]:
    """Map path -> hits. `staged` reads the INDEX, not the working tree.

    That distinction is the point when this runs from `update-readme`: the
    report has to describe what is about to be committed, not whatever the
    developer happens to have open and half-edited.
    """
    found: dict[str, list[Hit]] = {}
    for path in paths:
        if staged:
            source = _git("show", f":{path}")
        else:
            try:
                source = Path(path).read_text(encoding="utf-8")
            except OSError as exc:
                raise DetectorError(f"{path}: could not read: {exc}") from exc
        hits = find_hits_in_source(source, path)
        if hits:
            found[path] = hits
    return found


# --------------------------------------------------------------------------
# git plumbing
# --------------------------------------------------------------------------


def _git(*args: str) -> str:
    # encoding="utf-8" is NOT optional: `text=True` alone decodes with the
    # LOCALE codec, which on a Windows shell is cp1252 — and `git show :path`
    # returns whole source files, which in this repo are full of Spanish
    # accented text ("matrícula", "próximo"). Without it the read dies on the
    # first á, in a reader thread, and the caller gets None instead of source.
    try:
        completed = subprocess.run(
            ["git", *args], capture_output=True, text=True, encoding="utf-8", check=True, timeout=60
        )
    except (subprocess.SubprocessError, OSError, UnicodeDecodeError) as exc:
        raise DetectorError(f"git {' '.join(args)} failed: {exc}") from exc
    if completed.stdout is None:
        raise DetectorError(f"git {' '.join(args)} produced no output")
    return completed.stdout


def _python_files(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.strip().endswith(".py")]


def python_files_staged() -> list[str]:
    # ACMR: a DELETED file has no staged content to inspect and `git show :path`
    # on one is an error, which would be reported as a broken detector.
    return _python_files(_git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", PY_GLOB))


def python_files_in_range(rev_range: str) -> list[str]:
    return _python_files(_git("diff", "--name-only", "--diff-filter=ACMR", rev_range, "--", PY_GLOB))


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _render_text(found: dict[str, list[Hit]]) -> str:
    if not found:
        return "No non-top-level imports in the inspected files."
    total = sum(len(v) for v in found.values())
    out = [f"{total} non-top-level import(s) in {len(found)} file(s):", ""]
    for path, hits in found.items():
        out.append(path)
        for lineno, kind, context, source in hits:
            out.append(f"  L{lineno:<5} [{kind}] in {context or 'module level'}")
            out.append(f"         {source}")
        out.append("")
    return "\n".join(out).rstrip()


def _render_markdown(found: dict[str, list[Hit]]) -> str:
    if not found:
        return ""
    total = sum(len(v) for v in found.values())
    out = [
        "> [!NOTE]",
        f"> **{total} non-top-level import(s)** in {len(found)} staged file(s) — each needs a reason.",
        "",
        "| File | Line | Shape | Import |",
        "| --- | --- | --- | --- |",
    ]
    for path, hits in found.items():
        for lineno, kind, _context, source in hits:
            out.append(f"| `{path}` | {lineno} | {kind} | `{source}` |")
    return "\n".join(out)


def render(found: dict[str, list[Hit]], fmt: str) -> str:
    if fmt == "text":
        return _render_text(found)
    if fmt == "markdown":
        return _render_markdown(found)
    if fmt == "files":
        # Prints NOTHING when clean, on purpose: shells test this with
        # `[ -n "$X" ]`, so a friendly "nothing found" line here would mark
        # every clean release as having deferred imports.
        return "\n".join(sorted(found))
    raise DetectorError(f"unknown format: {fmt}")


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect imports that are not at module top level.")
    parser.add_argument("files", nargs="*", help="Python files to inspect")
    parser.add_argument("--staged", action="store_true", help="inspect staged Python files (reads the index)")
    parser.add_argument("--range", dest="rev_range", help="git revision range, e.g. main..testing")
    parser.add_argument("--format", choices=("text", "markdown", "files"), default="text")
    parser.add_argument(
        "--deferred-only",
        action="store_true",
        help="report only the 'deferred' shape — the one that is nearly always an accident",
    )
    parser.add_argument("--quiet", action="store_true", help="exit status only, print nothing")
    args = parser.parse_args(argv)

    try:
        if args.staged:
            paths = python_files_staged()
        elif args.rev_range:
            paths = python_files_in_range(args.rev_range)
        else:
            paths = args.files
        if not paths:
            if not args.quiet and args.format != "files":
                print(render({}, args.format))
            return 0
        found = find_hits(paths, staged=bool(args.staged))
        if args.deferred_only:
            found = {p: [h for h in hits if h[1] == "deferred"] for p, hits in found.items()}
            found = {p: hits for p, hits in found.items() if hits}
    except DetectorError as exc:
        print(f"detect_non_top_level_imports: {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        text = render(found, args.format)
        if text:
            print(text)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
