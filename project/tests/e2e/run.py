"""Run every end-to-end journey in this directory.

    make e2e                          all journeys
    make e2e ARGS="--list"            what exists
    make e2e ARGS="--only payment"    one of them
    make e2e ARGS="--keep"            skip cleanup, leave the evidence behind

    docker compose exec web python project/tests/e2e/run.py --only payment

THE SINGLE ENTRY POINT, on purpose. It owns the Django bootstrap, the safety
refusal and the environment substitutions below, so a journey module is an
ordinary importable module with a `run()` — no per-file `django.setup()`, no
per-file copy of the rules, and no way for two journeys to disagree about the
environment they are testing in.

IT SHIPS. This used to live under `scripts/`, which `.dockerignore` drops;
`project/tests/` it does NOT drop (that pattern is anchored at the build-context
root and there is no root-level `tests/`), so these files are inside the
production image. The refusal in `main()` is therefore the only thing standing
between a journey and the academy's real database — they create and delete rows
by primary key. Keep it first, keep it reading the REAL `settings.ENVIRONMENT`,
and never move the forced substitution below it.

TWO DELIBERATE SUBSTITUTIONS, both required to make a run honest rather than
merely green:

* `ENVIRONMENT` is forced to "production" in-process. The Drive archive is
  production-only by design (`drive_uploads_allowed`), so without this a journey
  would exercise nothing and still pass.
* `CELERY_TASK_ALWAYS_EAGER` is forced on, as production runs it (Cloud Run has
  no worker). This dev stack HAS Redis, so `.delay()` would hand the work to the
  worker container — a different process, where neither of these substitutions is
  set. That is how an early run of the payment journey silently archived nothing
  and reported success.

Email goes through the locmem backend so a journey can assert on the message.
The point is that a receipt is produced, addressed to the right family and
carries its PDF — not that Gmail accepts it.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import traceback
from pathlib import Path
from typing import Any

# project/tests/e2e/run.py -> parents[2] is project/, which is what `manage.py`
# runs from and what pytest.ini sets as the pythonpath.
BASE = Path(__file__).resolve().parents[2]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

from tests.e2e._harness import FAILED, PASSED, UNVERIFIED, Report, Unverified  # noqa: E402

#: A journey is any `*_journey.py` in this directory exposing NAME, SUMMARY and
#: run(report, keep). Discovery is by suffix rather than a hand-kept list so a
#: new file cannot be added and silently never run.
JOURNEY_SUFFIX = "_journey.py"

RULE = "=" * 72


def discover() -> list[tuple[str, Any]]:
    """Import every journey module, sorted by filename for a stable order."""
    here = Path(__file__).resolve().parent
    modules: list[tuple[str, Any]] = []
    for path in sorted(here.glob(f"*{JOURNEY_SUFFIX}")):
        module = importlib.import_module(f"tests.e2e.{path.stem}")
        for attribute in ("NAME", "SUMMARY", "run"):
            if not hasattr(module, attribute):
                raise SystemExit(f"{path.name} is missing `{attribute}` — see tests/e2e/run.py for the contract.")
        modules.append((module.NAME, module))
    return modules


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the end-to-end journeys.")
    parser.add_argument("--only", metavar="NAME", help="run just the journeys whose name contains NAME")
    parser.add_argument("--keep", action="store_true", help="skip cleanup so the results can be inspected by hand")
    parser.add_argument("--list", action="store_true", help="list the journeys and exit")
    args = parser.parse_args()

    # Read the REAL value before the substitution below forces it. This file
    # ships inside the image; this line is the guard, not a convenience.
    if settings.ENVIRONMENT == "production":
        print("REFUSING: these journeys create and delete rows by primary key. Never production.")
        return UNVERIFIED

    journeys = discover()
    if args.only:
        journeys = [(name, module) for name, module in journeys if args.only.lower() in name.lower()]
        if not journeys:
            print(f"No journey matches --only {args.only!r}. Try --list.")
            return UNVERIFIED

    if args.list:
        for name, module in journeys:
            print(f"  {name:<16} {module.SUMMARY}")
        return PASSED

    settings.ENVIRONMENT = "production"  # the Drive gate is production-only
    settings.CELERY_TASK_ALWAYS_EAGER = True  # as production runs
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    # `django.test.Client` sends Host: testserver, which dev's ALLOWED_HOSTS
    # (localhost,127.0.0.1) rejects with a 400 — and a journey posting to a real
    # view is worth far more than one calling the service behind it.
    if "testserver" not in settings.ALLOWED_HOSTS:
        settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "testserver"]

    results: list[tuple[str, int, str]] = []
    for name, module in journeys:
        print(f"\n{RULE}\n  JOURNEY: {name} — {module.SUMMARY}\n{RULE}")
        report = Report(name)
        try:
            module.run(report, keep=args.keep)
        except Unverified as exc:
            results.append((name, UNVERIFIED, str(exc)))
            print(f"\n   COULD NOT VERIFY: {exc}")
            continue
        except Exception:  # noqa: BLE001 — an unhandled error in ONE journey must not lose the rest
            traceback.print_exc()
            results.append((name, FAILED, "unhandled exception — traceback above"))
            continue
        if report.passed:
            results.append((name, PASSED, "every check passed"))
        else:
            results.append((name, FAILED, f"{len(report.failures)} check(s): {', '.join(report.failures)}"))

    print(f"\n{RULE}\n  SUMMARY\n{RULE}")
    labels = {PASSED: "PASSED", FAILED: "FAILED", UNVERIFIED: "UNVERIFIED"}
    for name, code, detail in results:
        print(f"   [{labels[code]:^10}] {name:<16} {detail}")

    # FAILED outranks UNVERIFIED: if anything actually broke, say so, even when
    # something else could not run.
    codes = {code for _, code, _ in results}
    if FAILED in codes:
        return FAILED
    if UNVERIFIED in codes:
        return UNVERIFIED
    return PASSED


if __name__ == "__main__":
    raise SystemExit(main())
