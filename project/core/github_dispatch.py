"""
GitHub `repository_dispatch` notification for the QA sign-off.

When QA presses "¿Listo para desplegar?" on /testing/ (or an admin runs
`manage.py set_ready_for_prod on`), the production deploy workflow should
re-evaluate immediately instead of waiting for the next nightly testing run.
`notify_github_qa_signoff()` fires a `repository_dispatch` event
(`qa-ready-for-prod`) that `.github/workflows/deploy-production.yml` listens
to; its preflight then re-derives everything it needs from main's
`pyproject.toml` and testing's `/health/?deep=1` — the event carries no
payload the workflow trusts.

Deliberately FAIL-SOFT: the `ready_for_prod` flag in the database is the
source of truth and the nightly `workflow_run` re-trigger remains the fallback
arming path, so a missing token or an unreachable GitHub API must never make
the sign-off itself fail. Every early return and every error path logs and
returns False; the flag stays set either way.

Configuration (testing VM only — the helper is inert unless IS_TESTING_ENV):
- `GITHUB_DISPATCH_TOKEN` — fine-grained PAT scoped to this repository with
  `contents: read and write` (what the repository_dispatch endpoint requires).
  A leaked token can at worst make the credential-free preflight re-run: the
  gates and the human `production` environment approval still stand.
- `GITHUB_DISPATCH_REPO` — optional `owner/repo` override.
"""

import logging
import os

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

DISPATCH_EVENT_TYPE = "qa-ready-for-prod"
_DEFAULT_REPO = "starseeker-code-public/five-a-day"


def notify_github_qa_signoff() -> bool:
    """Fire the repository_dispatch that re-evaluates `Deploy production`.

    Returns True only when GitHub accepted the event. False means "not sent"
    for any reason (wrong environment, no token, network/API failure) — the
    caller should treat that as "the nightly run will pick the sign-off up
    instead", never as an error to surface.
    """
    if not settings.IS_TESTING_ENV:
        return False

    token = os.getenv("GITHUB_DISPATCH_TOKEN", "").strip()
    if not token:
        logger.warning(
            "GITHUB_DISPATCH_TOKEN is not set — 'Deploy production' will pick the "
            "QA sign-off up on its next nightly run instead of arming now."
        )
        return False

    repo = os.getenv("GITHUB_DISPATCH_REPO", "").strip() or _DEFAULT_REPO
    try:
        response = httpx.post(
            f"https://api.github.com/repos/{repo}/dispatches",
            json={"event_type": DISPATCH_EVENT_TYPE},
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10.0,
        )
        response.raise_for_status()
    except Exception:  # noqa: BLE001 — fail-soft by design; the nightly run is the fallback
        logger.exception(
            "repository_dispatch to GitHub failed — the sign-off flag is set and the "
            "nightly 'Deploy production' re-trigger remains the fallback arming path"
        )
        return False

    logger.info("repository_dispatch '%s' sent to %s", DISPATCH_EVENT_TYPE, repo)
    return True
