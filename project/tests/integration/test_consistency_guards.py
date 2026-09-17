"""Guards for rules that are deliberately written down TWICE.

Every test here pins a pair of sources that must agree and today do. None of
them is a bug report: each one exists because the codebase's recurring failure
is not an incorrect rule, it is a correct rule applied in one place and missed
in its sibling — a schedule type added to the ficha but not the invoice, a
palette pasted into three shells and drifting in one, a redaction set that
covers the field named `dni` and not the field beside it.

A duplication that cannot be removed (hand-written CSS cannot read a JS object;
JavaScript cannot import a Python service) is legitimate. A duplication with
nothing holding the two halves together is the bug waiting to happen. These are
the holding.

`test_frontend_invariants.py` does the same job for template/JS/CSS
invariants and is the better home for anything needing no database; these need
a rendered view or the ORM.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from pathlib import Path

import pytest
from django.urls import reverse

from billing.forms import ENROLLMENT_PLAN_CHOICES
from billing.models import Expense
from billing.services.payment_service import PaymentService
from core.middleware import QAErrorEmailMiddleware
from core.models import BacklogTask
from core.views.expenses import _VALID_CATEGORIES, _VALID_FREQUENCIES
from core.views.testing_tools import VALID_PRIORITIES

PROJECT_DIR = Path(__file__).resolve().parents[2]


# ───────────────────────────────────────────────────────────────────────────────
# Python rules mirrored into JavaScript
# ───────────────────────────────────────────────────────────────────────────────


class TestJavaScriptMirrorsOfPythonRules:
    """The preview an admin quotes to a family must match the invoice billed to it.

    There is no JS test runner in this project, so nothing ever EXECUTES these
    modules — `test_frontend_invariants.py` proves they avoid the forbidden
    idioms, which is a different question from whether their arithmetic is
    right. These two mirrors are arithmetic, and CLAUDE.md names both as rules
    that have to move in lockstep with their Python originals.
    """

    def test_the_proration_mirror_matches_payment_service(self):
        """`student-create.js` re-implements `PaymentService.proration_fraction`.

        The JS is one expression — `(daysInMonth - d + 1) / daysInMonth`, with
        the 1st of the month short-circuited to a whole period. If the Python
        rule moves and this does not, the amber "Primer pago (parcial)" row on
        the create form quotes one figure and the generated Payment bills
        another, for the same student on the same day, with nothing erroring.

        Checked over every day of a 365-day year plus a leap February, because
        the two formulations differ only at month boundaries.
        """

        source = (PROJECT_DIR / "core" / "static" / "js" / "student-create.js").read_text(encoding="utf-8")
        assert "(daysInMonth - d + 1) / daysInMonth" in source, (
            "the JS proration expression has changed shape; re-derive the mirror below from it"
        )

        mismatches = []
        for year, months in ((2026, range(1, 13)), (2028, [2])):  # 2028 for the leap February
            for month in months:
                days_in_month = calendar.monthrange(year, month)[1]
                for day in range(1, days_in_month + 1):
                    python = PaymentService.proration_fraction(date(year, month, day), month, year)
                    javascript = 1.0 if day <= 1 else (days_in_month - day + 1) / days_in_month
                    if abs(float(python) - javascript) > 1e-9:
                        mismatches.append(f"{year}-{month:02d}-{day:02d}: py={python} js={javascript}")
        assert not mismatches, (
            "student-create.js and PaymentService.proration_fraction disagree, so the "
            f"first-period preview and the first-period invoice differ: {mismatches[:10]}"
        )

    @pytest.mark.django_db
    def test_price_config_has_a_key_for_every_enrollment_plan(self, authenticated_client, site_config):
        """`student-create.js` reads `priceConfig[planSelect.value]`.

        A plan added to `ENROLLMENT_PLAN_CHOICES` without a matching key in
        `StudentCreateView.get_context_data`'s `price_config` does not raise in
        JavaScript — it yields `undefined` and the price widget previews **0
        euros**, which is a wrong number on the screen the admin reads the price
        off to the family.
        """

        price_config = authenticated_client.get(reverse("student_create")).context["price_config"]
        missing = [value for value, _ in ENROLLMENT_PLAN_CHOICES if value not in price_config]
        assert not missing, (
            f"enrollment plans with no price_config entry — student-create.js previews 0 euros for them: {missing}"
        )


# ───────────────────────────────────────────────────────────────────────────────
# Choice sets retyped outside the model
# ───────────────────────────────────────────────────────────────────────────────


class TestChoiceSetsAreDerivedFromTheModel:
    """A view that validates against `choices` must read them, not retype them.

    `core/views/expenses.py` builds its sets with a comprehension over
    `Expense.EXPENSE_CATEGORY_CHOICES`, which cannot drift.
    `core/views/testing_tools.py` writes the same kind of set out by hand, in
    two different styles. Adding a fourth priority or status to `BacklogTask`
    would leave those two endpoints answering 400 for a value the model, the
    admin and the server-rendered list all accept.
    """

    def test_backlog_priorities_match_the_model(self):
        assert VALID_PRIORITIES == {value for value, _ in BacklogTask.PRIORITY_CHOICES}, (
            "testing_tools.VALID_PRIORITIES has drifted from BacklogTask.PRIORITY_CHOICES; "
            "derive it the way core/views/expenses.py derives _VALID_CATEGORIES"
        )

    def test_backlog_statuses_match_the_model(self):
        """The status set is INLINE at the call site, so it is read out of the source.

        When `api_update_backlog_task` is changed to use a named, derived set
        (see the finding), replace this with a direct comparison against it.
        """

        source = (PROJECT_DIR / "core" / "views" / "testing_tools.py").read_text(encoding="utf-8")
        literal = re.search(r"new_status not in \(([^)]*)\)", source)
        if literal is None:  # already derived from the model — nothing left to drift
            return
        retyped = set(re.findall(r'"([^"]+)"', literal.group(1)))
        assert retyped == {value for value, _ in BacklogTask.STATUS_CHOICES}, (
            "the inline status tuple in api_update_backlog_task has drifted from "
            f"BacklogTask.STATUS_CHOICES: {sorted(retyped)}"
        )

    def test_expense_categories_and_frequencies_are_still_derived(self):
        """The good pattern, pinned so a later edit cannot retype these too."""

        assert _VALID_CATEGORIES == {value for value, _ in Expense.EXPENSE_CATEGORY_CHOICES}
        assert _VALID_FREQUENCIES == {value for value, _ in Expense.RECURRING_FREQUENCY_CHOICES}


# ───────────────────────────────────────────────────────────────────────────────
# The redaction set vs what the forms actually post
# ───────────────────────────────────────────────────────────────────────────────


def _password_input_names() -> set[str]:
    """Every `name=` on a `type="password"` input in any template."""
    names: set[str] = set()
    for directory in (PROJECT_DIR / "core" / "templates", PROJECT_DIR / "templates"):
        for path in directory.rglob("*.html"):
            for tag in re.findall(r"<input\b[^>]*?>", path.read_text(encoding="utf-8"), re.S):
                if 'type="password"' not in tag:
                    continue
                match = re.search(r'name="([^"]+)"', tag)
                if match:
                    names.add(match.group(1))
    return names


class TestEveryPostedCredentialIsRedacted:
    """`REDACT_KEYS` is read by the QA error mailer AND by
    `RedactingExceptionReporterFilter`, which scrubs every production error mail
    to `ADMINS`. A credential field added to a form and not to that set is
    mailed in cleartext on the next 500 — and the miss is silent, because
    nothing goes wrong until something else already has.

    `test_auth_hardening.py` parametrises the same set over the three body
    ENCODINGS. This asks the other question: is the set complete with respect to
    what the templates actually post?
    """

    def test_the_sweep_finds_the_password_fields(self):
        """Guard the guard: a regex that matches nothing would pass silently."""
        names = _password_input_names()
        assert len(names) >= 6, f"the password-input sweep found only {names} — has the markup changed?"

    def test_every_password_input_name_is_in_redact_keys(self):
        missing = sorted(_password_input_names() - set(QAErrorEmailMiddleware.REDACT_KEYS))
        assert not missing, (
            "these credential fields are posted by a template but are not in "
            f"QAErrorEmailMiddleware.REDACT_KEYS, so a 500 mails them in cleartext: {missing}"
        )
