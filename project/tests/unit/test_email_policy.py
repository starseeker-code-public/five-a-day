"""The public contact form's email rule.

`core.email_policy` is the only thing standing between a public, unauthenticated
form and the academy's inbox, so it is tested as a unit rather than through the
view: every branch is cheap here and expensive there.

Two properties are worth stating up front, because they are the ones a future
change is likely to trade away without noticing:

  * it is STRICTER than `django.core.validators.EmailValidator` on purpose, and
    several tests below assert exactly the forms Django accepts and this does
    not;
  * the domain allowlist REFUSES real people as a side effect of refusing
    throwaway addresses, so the refusal has to stay distinguishable from a
    syntax error — the view says something different for each, and it can only
    do that while the codes stay distinct.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator

from core.email_policy import (
    ALLOWED_EMAIL_DOMAINS,
    ERROR_DOMAIN_NOT_ALLOWED,
    ERROR_REQUIRED,
    ERROR_SYNTAX,
    ERROR_TOO_LONG,
    MAX_EMAIL_LENGTH,
    check_email,
    domain_of,
)


class TestAddressesThatPass:
    @pytest.mark.parametrize(
        "address",
        [
            "ana@gmail.com",
            "ana.garcia@gmail.com",
            "ana+academia@gmail.com",
            "ana_garcia@hotmail.es",
            "ana-garcia@outlook.es",
            "ana123@yahoo.es",
            "a@icloud.com",
            "familia.garcia.ruiz@telefonica.net",
        ],
    )
    def test_ordinary_family_addresses(self, address):
        normalised, error = check_email(address)
        assert error is None
        assert normalised == address

    def test_surrounding_whitespace_is_forgiven(self):
        """Pasted addresses routinely carry it; refusing that is pure friction."""
        assert check_email("  ana@gmail.com \n") == ("ana@gmail.com", None)

    def test_the_domain_is_lowercased_and_the_local_part_is_not(self):
        """RFC 5321 makes the local part case-SENSITIVE.

        Every provider on the allowlist happens to ignore case, but rewriting
        somebody's address is still not this function's decision — and the
        academy replies to whatever is stored.
        """
        assert check_email("Ana.Garcia@GMAIL.COM") == ("Ana.Garcia@gmail.com", None)

    def test_the_lowercased_domain_is_what_the_allowlist_sees(self):
        assert check_email("ana@HoTmAiL.Es")[1] is None


class TestAddressesThatAreRefused:
    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_an_empty_value_is_required_not_syntax(self, blank):
        """The view already reports missing required fields by name; this code
        exists so a blank never reads as a malformed address."""
        assert check_email(blank) == ("", ERROR_REQUIRED)

    @pytest.mark.parametrize(
        "address",
        [
            "ana",
            "ana@",
            "@gmail.com",
            "ana@@gmail.com",
            "ana@gmail@com",
            "ana@gmail",  # no TLD
            "ana@gmail.c",  # one-letter TLD
            "ana@.com",
            "ana@gmail..com",
            ".ana@gmail.com",
            "ana.@gmail.com",
            "an..a@gmail.com",
            "ana@-gmail.com",
            "ana@gmail-.com",
        ],
    )
    def test_malformed_addresses(self, address):
        assert check_email(address) == ("", ERROR_SYNTAX)

    @pytest.mark.parametrize(
        "address",
        [
            "ana garcia@gmail.com",
            "ana@gmail .com",
            "ana@gmail.com\nbcc: someone@evil.test",
            "ana\t@gmail.com",
        ],
    )
    def test_whitespace_inside_the_address(self, address):
        """A newline in a value that reaches a mail layer is the shape header
        injection takes. Django refuses it at the header, but refusing it here
        means the value never gets that far."""
        assert check_email(address) == ("", ERROR_SYNTAX)

    def test_non_ascii_is_refused(self):
        """SMTPUTF8 is legal and this academy's Gmail cannot be relied on to
        route it. Accepting an enquiry we then fail to answer is worse than
        refusing it plainly."""
        assert check_email("añana@gmail.com") == ("", ERROR_SYNTAX)

    def test_an_over_long_address(self):
        assert check_email("a" * MAX_EMAIL_LENGTH + "@gmail.com") == ("", ERROR_TOO_LONG)

    def test_an_over_long_local_part_within_the_total_cap(self):
        assert check_email("a" * 65 + "@gmail.com") == ("", ERROR_TOO_LONG)

    def test_length_is_checked_before_anything_scans_the_string(self):
        """The cap is what bounds the work this function does on a hostile
        input, so it must not sit behind a regex."""
        assert check_email("a" * 5000 + " @ " + "b" * 5000) == ("", ERROR_TOO_LONG)


class TestStricterThanDjango:
    """Each of these is accepted by `EmailValidator` and refused here.

    The test asserts BOTH halves — that Django accepts it and that we do not —
    so it fails loudly if a future Django tightens up and the comment justifying
    our extra rule quietly stops being true.
    """

    @pytest.mark.parametrize(
        "address",
        [
            '"ana.garcia"@gmail.com',  # quoted local part: can hide an @ or a comma
            '"ana@evil.test"@gmail.com',  # ...and here it does
            "ana@[192.168.0.1]",  # address literal: no domain to check at all
        ],
    )
    def test_forms_django_allows_but_a_family_never_types(self, address):
        EmailValidator()(address)  # raises if Django ever stops allowing it
        assert check_email(address)[1] == ERROR_SYNTAX

    def test_django_would_accept_an_unknown_domain(self):
        """The allowlist is ours alone — syntax validation cannot see the
        difference between a family's mailbox and a ten-minute one."""
        EmailValidator()("ana@mailinator.com")
        assert check_email("ana@mailinator.com")[1] == ERROR_DOMAIN_NOT_ALLOWED


class TestTheDomainAllowlist:
    @pytest.mark.parametrize(
        "address",
        [
            "ana@mailinator.com",
            "ana@guerrillamail.com",
            "ana@10minutemail.com",
            "ana@yopmail.com",
            "ana@tempmail.net",
            "ana@sharklasers.com",
        ],
    )
    def test_throwaway_providers_are_refused(self, address):
        assert check_email(address)[1] == ERROR_DOMAIN_NOT_ALLOWED

    def test_a_refused_domain_is_not_reported_as_a_syntax_error(self):
        """The view says something different for each, and the domain message is
        the one that has to offer a way round — an allowlist occasionally turns
        away a real family with a work address."""
        assert check_email("ana@una-empresa-real.es")[1] == ERROR_DOMAIN_NOT_ALLOWED
        assert check_email("ana@@gmail.com")[1] == ERROR_SYNTAX

    def test_a_subdomain_of_an_allowed_domain_is_not_allowed(self):
        """`endswith` matching would accept `gmail.com.evil.test`; membership
        does not, and that is why the check is membership."""
        assert check_email("ana@mail.gmail.com")[1] == ERROR_DOMAIN_NOT_ALLOWED
        assert check_email("ana@gmail.com.evil.test")[1] == ERROR_DOMAIN_NOT_ALLOWED

    def test_every_entry_is_lowercase_and_bare(self):
        """Entries are compared against a lowercased domain, so an uppercase or
        `@`-prefixed entry would be dead weight that silently matches nothing."""
        for domain in ALLOWED_EMAIL_DOMAINS:
            assert domain == domain.strip().lower(), domain
            assert not domain.startswith("@"), domain
            assert "." in domain, domain

    def test_every_entry_is_actually_reachable_by_the_validator(self):
        """A typo in the list is invisible: the entry simply never matches, and
        the family it was added for keeps being refused."""
        for domain in sorted(ALLOWED_EMAIL_DOMAINS):
            assert check_email(f"ana@{domain}") == (f"ana@{domain}", None), domain

    def test_the_big_four_providers_are_present(self):
        """Not a style rule — between them these carry most of the addresses the
        academy will ever be given, so losing one is a silent outage."""
        assert {"gmail.com", "hotmail.com", "outlook.com", "icloud.com"} <= ALLOWED_EMAIL_DOMAINS


class TestDomainOf:
    def test_it_returns_the_domain_for_logging(self):
        assert domain_of("Ana@Gmail.com") == "gmail.com"

    def test_it_survives_rubbish_without_raising(self):
        """It is called on values that have ALREADY been refused, so it is only
        ever handed input that failed validation."""
        assert domain_of("not-an-email") == ""
        assert domain_of("") == ""
        assert domain_of(None) == ""

    def test_it_never_returns_the_local_part(self):
        """The whole point: this endpoint is public, so the address is a
        stranger's personal data and must not reach the log."""
        assert "ana" not in domain_of("ana@gmail.com")

    def test_it_is_bounded(self):
        assert len(domain_of("a@" + "b" * 5000)) <= 253


def test_a_valid_address_never_raises_for_any_allowed_domain():
    """Property check over the whole list: `check_email` is called on a request
    path whose contract is a JSON answer, so it must not raise."""
    for domain in ALLOWED_EMAIL_DOMAINS:
        for local in ("a", "ana.garcia", "ana+tag", "ana_garcia-1"):
            try:
                assert check_email(f"{local}@{domain}")[1] is None
            except ValidationError as exc:  # pragma: no cover - the assertion is the test
                pytest.fail(f"{local}@{domain} raised {exc}")
