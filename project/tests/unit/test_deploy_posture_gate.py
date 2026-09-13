"""`POSTURE_ENV_KEYS` in deploy-production.yml must track the guard in settings.py.

Gate 2 of `.github/workflows/deploy-production.yml` asserts that every Cloud Run
JOB carries the same posture env vars as the service. That list is hand-written
in a YAML file; the rule it exists to enforce lives in `project/settings.py`. The
two can drift in silence, and they drift in opposite, equally bad directions:

* a key in settings.py with no entry in the workflow is a var the gate does not
  compare, so the service can hold it and the twelve jobs not — which is exactly
  how `CACHE_DB` reached production. Every job was missing it, the inventory gate
  passed 12/12 because it only asked whether the jobs EXISTED, the deploy
  repointed them all at the new image, and `migrate` then died at settings import
  with "CACHE_URL or CACHE_DB must be set in production". Had migrate not run
  first and tripped the rollback, all twelve scheduled tasks would have stopped
  running and nothing would have said so.
* a key in the workflow that the guard does not actually care about is a false
  positive waiting to fail a real deploy. `DJANGO_ALLOWED_HOSTS` was one: the
  first draft of the gate demanded equality, but the service legitimately carries
  the alternate run.app domain and 127.0.0.1 for its probes while a job serves no
  HTTP and carries neither. The guard only asks that '*' is absent.

So this is the same consolidation as `core.decorators.may_use_qa_tools` (v1.29.0)
and `billing.money.monthly_fee_for` (v1.29.1): where two copies of one rule had
already drifted, pin them to each other with a test. It cannot be a shared
constant — one side is a GitHub Actions env block that never imports Python.

Both halves below load `settings.py` in ISOLATION under a private module name, so
Django's configured settings are untouched, reusing the pattern from
`TestDatabaseConnectionSettings` in tests/integration/test_query_cost_and_idempotency.py.
"""

import importlib.util
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings as dj_settings

# ---------------------------------------------------------------------------
# Locating the two sources of truth
# ---------------------------------------------------------------------------

SETTINGS_PATH = Path(dj_settings.BASE_DIR) / "project" / "settings.py"
WORKFLOW_PATH = Path(dj_settings.BASE_DIR).parent / ".github" / "workflows" / "deploy-production.yml"

# Settings names the guard reads -> the env var that sets them. Anything the
# guard touches must appear here, so a NEW assertion added to settings.py fails
# this file until somebody decides which bucket it belongs in. That forced
# decision is the whole point: the alternative is a silent no-op in the gate.
SETTING_TO_ENV = {
    "DEBUG": "DJANGO_DEBUG",
    "DJANGO_DEBUG": "DJANGO_DEBUG",
    "ENVIRONMENT": "DJANGO_ENV",
    "ALLOWED_HOSTS": "DJANGO_ALLOWED_HOSTS",
    "DJANGO_ALLOWED_HOSTS": "DJANGO_ALLOWED_HOSTS",
    "SESSION_COOKIE_SECURE": "SESSION_COOKIE_SECURE",
    "CSRF_COOKIE_SECURE": "CSRF_COOKIE_SECURE",
    "SECURE_SSL_REDIRECT": "SECURE_SSL_REDIRECT",
    "SESSION_COOKIE_HTTPONLY": "SESSION_COOKIE_HTTPONLY",
    "SECURE_HSTS_SECONDS": "SECURE_HSTS_SECONDS",
    "CACHES": "CACHE_DB",
    "CACHE_DB": "CACHE_DB",
    "CACHE_URL": "CACHE_URL",
}

# Env vars the gate deliberately does NOT compare for equality, each with its
# own rule in the workflow. Listed here so they still count as "covered".
EQUALITY_EXEMPT = {
    # The guard accepts EITHER backend, so the gate asserts "CACHE_DB or
    # CACHE_URL is set" rather than naming one — which would also break the day
    # this moves to Redis.
    "CACHE_DB",
    "CACHE_URL",
    # Differs correctly between service and job; the gate asserts "not empty and
    # contains no '*'", which is the only thing the guard itself asks.
    "DJANGO_ALLOWED_HOSTS",
}

# Structural tokens that are not settings at all — dict keys and locals the
# regex cannot tell apart from a setting name.
NOT_A_SETTING = {"BACKEND"}


def _guard_source() -> str:
    """The `if ENVIRONMENT == "production":` block, comments stripped.

    Comments are dropped before the token scan because the block's prose names
    half the vars in the file (`CACHE_DB`, `--set-env-vars`, `LocMemCache`) and
    would swamp the real references.
    """
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    start = source.index('if ENVIRONMENT == "production":')
    end = source.index("del _posture_errors", start)
    block = source[start:end]
    return "\n".join(re.sub(r"#.*$", "", line) for line in block.splitlines())


def _settings_referenced_by_guard() -> set[str]:
    return set(re.findall(r"[A-Z][A-Z0-9_]{3,}", _guard_source()))


def _workflow_posture_keys() -> list[str]:
    """Read POSTURE_ENV_KEYS out of the workflow's `>-` folded block.

    Parsed by hand rather than with a YAML library: the repo has no yaml
    dependency outside the test extras, and the shape here is fixed.
    """
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    match = re.search(r"^  POSTURE_ENV_KEYS: >-\n((?:    \S+\n)+)", text, re.MULTILINE)
    assert match, "POSTURE_ENV_KEYS is missing from deploy-production.yml, or its shape changed"
    return match.group(1).split()


# ---------------------------------------------------------------------------
# Loading settings.py in isolation
# ---------------------------------------------------------------------------

# A baseline that PASSES the guard. Every var the guard reads is set explicitly,
# because `settings.py` calls `load_dotenv(".env")` at import and the developer's
# own file is therefore in `os.environ` for the whole session — inheriting any of
# these would make the test pass or fail depending on whose machine it ran on.
PRODUCTION_BASELINE = {
    "DJANGO_ENV": "production",
    "DJANGO_DEBUG": "False",
    "DJANGO_SECRET_KEY": "probe-only-not-a-real-key",
    "DJANGO_ALLOWED_HOSTS": "fiveaday.example.run.app",
    "SESSION_COOKIE_SECURE": "True",
    "CSRF_COOKIE_SECURE": "True",
    "SECURE_SSL_REDIRECT": "True",
    "SESSION_COOKIE_HTTPONLY": "True",
    "SECURE_HSTS_SECONDS": "31536000",
    "CACHE_DB": "True",
    "CACHE_URL": "",
}

# The value that makes each key INSECURE. Flipping one and re-importing must
# raise: that is what proves the key is genuinely load-bearing and not a stale
# entry the gate compares for nothing.
INSECURE_VALUE = {
    "DJANGO_DEBUG": "True",
    "SESSION_COOKIE_SECURE": "False",
    "CSRF_COOKIE_SECURE": "False",
    "SECURE_SSL_REDIRECT": "False",
    "SESSION_COOKIE_HTTPONLY": "False",
    "SECURE_HSTS_SECONDS": "3600",
}


def _load_settings(**env):
    """Import settings.py under a throwaway module name with `env` applied."""
    spec = importlib.util.spec_from_file_location("_posture_probe_settings", SETTINGS_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {**PRODUCTION_BASELINE, **env}, clear=False):
        spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------


class TestTheBaselineIsActuallyProduction:
    """If the baseline stopped being a passing production config, every test in
    the class below would pass vacuously — a flipped key would raise for the
    wrong reason, or the baseline would raise before any key was flipped."""

    def test_the_baseline_starts_cleanly(self):
        module = _load_settings()
        assert module.ENVIRONMENT == "production"
        assert module.DEBUG is False

    def test_the_baseline_really_does_run_the_guard(self):
        """`DJANGO_ENV` is what arms the whole block; without it the flips below
        would be asserting nothing at all."""
        with pytest.raises(ValueError, match="Insecure production configuration"):
            _load_settings(CACHE_DB="False", CACHE_URL="")


class TestEveryGuardedSettingIsCoveredByTheGate:
    """Drift direction 1: settings.py grows an assertion the workflow ignores."""

    def test_every_setting_the_guard_reads_is_mapped(self):
        unmapped = _settings_referenced_by_guard() - set(SETTING_TO_ENV) - NOT_A_SETTING
        assert not unmapped, (
            f"settings.py's production posture guard now reads {sorted(unmapped)}, which this test "
            "cannot map to an env var. Add it to SETTING_TO_ENV, then decide whether "
            "POSTURE_ENV_KEYS in .github/workflows/deploy-production.yml must compare it across the "
            "Cloud Run jobs — a job missing a posture var does not fail its task, it fails to START, "
            "and a Cloud Scheduler trigger reports nothing when it does."
        )

    def test_every_guarded_env_var_is_checked_by_the_deploy_gate(self):
        keys = set(_workflow_posture_keys())
        required = {SETTING_TO_ENV[name] for name in _settings_referenced_by_guard() & set(SETTING_TO_ENV)}
        missing = required - keys - EQUALITY_EXEMPT
        assert not missing, (
            f"{sorted(missing)} drive the production posture guard but are not in POSTURE_ENV_KEYS in "
            ".github/workflows/deploy-production.yml, so the deploy gate will not compare them "
            "between the service and the twelve Cloud Run jobs. This is the CACHE_DB shape: the "
            "inventory passed 12/12 on job EXISTENCE while every job was unable to start."
        )


class TestEveryGateKeyIsLoadBearing:
    """Drift direction 2: the workflow compares a key the guard ignores.

    A dead entry is not harmless — it is a false positive that fails a real
    deploy for a difference that is correct, which is how `DJANGO_ALLOWED_HOSTS`
    behaved before it was moved to its own rule.
    """

    def test_no_gate_key_is_unknown_to_this_test(self):
        unknown = set(_workflow_posture_keys()) - set(SETTING_TO_ENV.values())
        assert not unknown, (
            f"POSTURE_ENV_KEYS lists {sorted(unknown)}, which the posture guard in settings.py does "
            "not read. Either the guard lost an assertion and the workflow was not updated, or the "
            "key was never load-bearing — a key compared for nothing fails deploys on differences "
            "that are legitimate."
        )

    @pytest.mark.parametrize("key", sorted(INSECURE_VALUE))
    def test_flipping_the_key_trips_the_guard(self, key):
        """Behavioural proof, not a string comparison: set the key to its
        insecure value and settings.py must refuse to import."""
        if key in _workflow_posture_keys():
            with pytest.raises(ValueError, match="Insecure production configuration"):
                _load_settings(**{key: INSECURE_VALUE[key]})

    def test_every_flippable_key_is_actually_on_the_gate(self):
        """Keeps the parametrised test above honest — it skips its assertion for
        a key absent from the workflow, so without this, deleting a key from
        POSTURE_ENV_KEYS would silently turn that case into a no-op."""
        assert set(INSECURE_VALUE) <= set(_workflow_posture_keys())


class TestTheTwoExemptionsKeepTheirOwnRule:
    """The exempt vars are exempt from EQUALITY, not from being checked. Each
    has a bespoke assertion in the workflow; if one disappears, the var stops
    being checked at all and nothing else notices."""

    def test_the_cache_is_asserted_as_either_backend(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "(CACHE_DB|CACHE_URL)=" in text, (
            "the deploy gate no longer asserts that each Cloud Run job sets a cache backend. "
            "settings.py refuses to start in production without one, so every job would fail at "
            "import — the exact v1.29.3 failure."
        )

    def test_allowed_hosts_is_asserted_as_no_wildcard(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "DJANGO_ALLOWED_HOSTS=" in text and "WILDCARD" in text.upper(), (
            "the deploy gate no longer checks DJANGO_ALLOWED_HOSTS on the jobs. It is exempt from "
            "the equality comparison (the service carries hosts a job has no use for), so this "
            "bespoke rule is the ONLY thing checking it."
        )

    def test_a_wildcard_host_really_is_refused_by_the_guard(self):
        """Pins the rule the workflow's bespoke check mirrors."""
        with pytest.raises(ValueError, match="Insecure production configuration"):
            _load_settings(DJANGO_ALLOWED_HOSTS="*")

    def test_no_cache_backend_really_is_refused_by_the_guard(self):
        with pytest.raises(ValueError, match="Insecure production configuration"):
            _load_settings(CACHE_DB="False", CACHE_URL="")

    def test_a_redis_cache_satisfies_the_guard(self):
        """Why the gate asks "CACHE_DB or CACHE_URL" instead of naming CACHE_DB:
        production uses the DB table today, but pinning the check to that one
        variable would fail the day this moves to Memorystore."""
        module = _load_settings(CACHE_DB="False", CACHE_URL="redis://cache.invalid:6379/0")
        assert "locmem" not in module.CACHES["default"]["BACKEND"].lower()
