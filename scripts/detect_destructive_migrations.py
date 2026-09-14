#!/usr/bin/env python3
"""Detect destructive operations in Django migration files.

THE single definition of "destructive" for this repository. Five surfaces ask
the question and they must not answer it differently:

  * ``deploy-production.yml`` preflight - warns the approver BEFORE they click
    approve, so a release needing ``ack_destructive`` is not discovered by
    burning an approval on a run that cannot pass the gate.
  * ``deploy-production.yml`` gate      - ENFORCES it (refuses without
    ``ack_destructive``). Policy lives in the workflow; detection lives here.
  * ``auto-merge.yml``                  - release PR body + merge email.
  * ``deploy-testing.yml``              - deploy-success email.
  * the ``update-readme`` skill         - final report to the developer.

It used to be an inline ``grep -E`` in the production workflow only, so the
other four surfaces said nothing at all: v1.29.5 shipped five ``RemoveField``s
and the first anyone heard of it was a gate refusing a deploy a human had
already approved. Copying the regex into each file instead would have been the
pattern this codebase keeps paying for (``may_use_qa_tools``, ``csv_safe``,
``monthly_fee_for`` - each a rule spelled twice that drifted).

Stdlib only, like ``check_version_coherence.py``: it runs on a bare GitHub
runner and in a repo checkout with no virtualenv.

Exit status is a REPORT, not a verdict - the caller owns the policy:

    0  no destructive operations found
    1  destructive operations found
    2  usage / internal error (callers must treat this as "assume the worst")

Usage::

    detect_destructive_migrations.py FILE [FILE ...]
    detect_destructive_migrations.py --range origin/main..origin/testing
    detect_destructive_migrations.py --staged

plus ``--format {text,markdown,html}``, ``--github-output`` and ``--quiet``.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Django operation classes: matched case-SENSITIVELY, they are Python names.
_OPERATIONS = ("DeleteModel", "RemoveField", "RenameField", "RenameModel")

# Raw SQL inside RunSQL: matched case-INSENSITIVELY. The shell original was
# case-sensitive throughout, so a RunSQL("drop table ...") walked straight past
# the production gate. Widening this can only ever flag MORE releases as
# destructive, which is the safe direction to be wrong in.
_SQL = (r"DROP\s+(?:TABLE|COLUMN)", r"TRUNCATE")

DESTRUCTIVE_RE = re.compile("|".join([*(re.escape(op) for op in _OPERATIONS), *(f"(?i:{s})" for s in _SQL)]))

MIGRATION_GLOB = "*/migrations/*.py"

ACK_HINT_TEXT = (
    "Deploying this to production requires the 'ack_destructive' input:\n"
    "  Actions -> Deploy production -> Run workflow -> tick ack_destructive\n"
    "Do NOT use 'force' -- it also skips the QA sign-off, the provenance gate\n"
    "and the version compare."
)


class DetectorError(RuntimeError):
    """Raised for anything that must NOT be read as 'nothing destructive'."""


def find_hits(paths: list[str]) -> dict[str, list[tuple[int, str]]]:
    """Map each file to its ``(line number, line text)`` destructive hits.

    A path that does not exist is skipped rather than raising: ``git diff
    --name-only`` lists deleted files too, and a migration deleted by the range
    under inspection cannot be applied by it.
    """
    hits: dict[str, list[tuple[int, str]]] = {}
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:  # an unreadable file must not read as clean
            raise DetectorError(f"cannot read {raw}: {exc}") from exc
        found = [(n, line.strip()) for n, line in enumerate(text.splitlines(), 1) if DESTRUCTIVE_RE.search(line)]
        if found:
            hits[raw] = found
    return hits


def _git(*args: str) -> str:
    try:
        completed = subprocess.run(["git", *args], capture_output=True, text=True, check=True, timeout=60)
    except (subprocess.SubprocessError, OSError) as exc:
        raise DetectorError(f"git {' '.join(args)} failed: {exc}") from exc
    return completed.stdout


def migrations_in_range(rev_range: str) -> list[str]:
    out = _git("diff", "--name-only", rev_range, "--", MIGRATION_GLOB)
    return [line for line in out.splitlines() if line.strip()]


def migrations_staged() -> list[str]:
    out = _git("diff", "--cached", "--name-only", "--", MIGRATION_GLOB)
    return [line for line in out.splitlines() if line.strip()]


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _render_text(hits: dict[str, list[tuple[int, str]]], files: list[str], many: bool) -> str:
    head = "DESTRUCTIVE MIGRATIONS DETECTED" if many else "DESTRUCTIVE MIGRATION DETECTED"
    lines = [f"{head} ({len(files)} file{'s' if many else ''})", ""]
    for name in files:
        lines.append(f"  {name}")
        lines.extend(f"    line {n}: {text}" for n, text in hits[name])
    lines += ["", ACK_HINT_TEXT]
    return "\n".join(lines)


def _render_markdown(hits: dict[str, list[tuple[int, str]]], files: list[str], many: bool) -> str:
    lines = [
        "> [!WARNING]",
        f"> **This release contains {len(files)} destructive migration{'s' if many else ''}.**",
        "> Deploying it to production requires the **`ack_destructive`** dispatch",
        "> input - *not* `force`, which would also skip the QA sign-off, the",
        "> provenance gate and the version compare.",
        "",
        "<details><summary>Destructive operations found</summary>",
        "",
    ]
    for name in files:
        lines.append(f"- `{name}`")
        lines.extend(f"  - line {n}: `{text}`" for n, text in hits[name])
    lines += [
        "",
        "Before approving, confirm the removal is **backward compatible**: migrations",
        "run BEFORE the service rolls, so the CURRENTLY DEPLOYED code runs against the",
        "new schema for the whole rollout window. If that old code still selects a",
        "dropped column, the window is an outage - split the change instead (ship the",
        "code that stops using the column first, drop it in the next release).",
        "",
        "</details>",
    ]
    return "\n".join(lines)


def _render_html(
    hits: dict[str, list[tuple[int, str]]],
    files: list[str],
    many: bool,
    applied: bool = False,
) -> str:
    items = []
    for name in files:
        ops = "<br>".join(f"&nbsp;&nbsp;linea {n}: <code>{_esc(text)}</code>" for n, text in hits[name])
        items.append(f"<li><code>{_esc(name)}</code><br>{ops}</li>")
    # Tense matters: the same block is mailed BEFORE a release ships (auto-merge,
    # testing deploy) and AFTER it has (the production success mail). "Will
    # require ack_destructive" read on a deploy-succeeded email is confusing at
    # exactly the moment somebody is deciding whether to worry.
    if applied:
        title = f"{len(files)} migraciones destructivas aplicadas" if many else "1 migracion destructiva aplicada"
        note = (
            "Estas columnas o tablas ya NO existen en la base de datos de "
            "produccion. La copia de seguridad previa al despliegue permite "
            "recuperarlas; restaurarla es una decision humana."
        )
    else:
        title = (
            f"{len(files)} migraciones destructivas en esta version"
            if many
            else "1 migracion destructiva en esta version"
        )
        note = (
            "El despliegue a produccion exigira el input <code>ack_destructive</code> "
            "(no <code>force</code>, que ademas se salta la validacion de QA, la de "
            "procedencia y la de version)."
        )
    return (
        '<div style="border-left:4px solid #b91c1c;background:#fef2f2;'
        'padding:12px 16px;margin:16px 0;font-family:sans-serif;">'
        f'<strong style="color:#b91c1c;">&#9888; {title}</strong>'
        f'<ul style="font-size:13px;margin:8px 0 0 0;">{"".join(items)}</ul>'
        f'<p style="font-size:13px;margin:8px 0 0 0;">{note}</p>'
        "</div>"
    )


def render(hits: dict[str, list[tuple[int, str]]], fmt: str, applied: bool = False) -> str:
    """Render a report. Returns an empty string when there is nothing to say.

    `applied=True` switches the HTML wording to past tense, for the mail sent
    once the migrations have actually run against production.
    """
    if not hits:
        return ""
    files = sorted(hits)
    many = len(files) > 1
    if fmt == "files":
        # Machine-readable: one path per line, nothing else. A shell caller can
        # word-split it straight into a `for` loop, and an empty result is the
        # honest representation of "clean" — which is why the `files` format is
        # the one case that must never print a friendly "nothing found" line.
        return "\n".join(files)
    if fmt == "text":
        return _render_text(hits, files, many)
    if fmt == "markdown":
        return _render_markdown(hits, files, many)
    if fmt == "html":
        return _render_html(hits, files, many, applied)
    raise DetectorError(f"unknown format: {fmt}")


def write_github_output(hits: dict[str, list[tuple[int, str]]], applied: bool = False) -> None:
    """Emit step outputs for a workflow. A no-op outside GitHub Actions."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    # Run-unique delimiter: the rendered blocks are multi-line and carry text
    # read out of files, so a fixed delimiter could be forged by a crafted
    # migration and inject extra step outputs.
    delim = f"FAD_DM_{os.urandom(12).hex()}"
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(f"destructive={'true' if hits else 'false'}\n")
        handle.write(f"destructive_count={len(hits)}\n")
        handle.write(f"destructive_files={' '.join(sorted(hits))}\n")
        for fmt in ("markdown", "html"):
            handle.write(f"destructive_{fmt}<<{delim}\n{render(hits, fmt, applied)}\n{delim}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect destructive operations in Django migration files.")
    parser.add_argument("files", nargs="*", help="migration files to inspect")
    parser.add_argument("--range", dest="rev_range", help="git revision range, e.g. main..testing")
    parser.add_argument("--staged", action="store_true", help="inspect staged migration files")
    parser.add_argument("--format", choices=("text", "markdown", "html", "files"), default="text")
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="also append step outputs to $GITHUB_OUTPUT",
    )
    parser.add_argument("--quiet", action="store_true", help="exit status only, print nothing")
    parser.add_argument(
        "--applied",
        action="store_true",
        help="past tense: the migrations have already run (post-deploy mail)",
    )
    args = parser.parse_args(argv)

    try:
        paths = list(args.files)
        if args.rev_range:
            paths += migrations_in_range(args.rev_range)
        if args.staged:
            paths += migrations_staged()

        hits = find_hits(sorted(set(paths)))

        if args.github_output:
            write_github_output(hits, args.applied)
        if not args.quiet:
            report = render(hits, args.format, args.applied)
            if report:
                print(report)
            elif args.format != "files":
                # `files` is parsed by a shell; every other format is read by a
                # human, who deserves to be told the scan ran and found nothing.
                print("No destructive migration operations found.")
    except DetectorError as exc:
        # Never exit 0 on an internal failure: every caller reads 0 as "safe".
        print(f"detect_destructive_migrations: {exc}", file=sys.stderr)
        return 2

    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
