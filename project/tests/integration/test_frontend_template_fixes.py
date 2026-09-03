"""Structural guards for the frontend fixes in v1.27.1.

Every test here is STATIC ANALYSIS over the template / JS / CSS sources rather
than a rendered response, and that is deliberate. The bugs this module pins
share a shape: they are invariants that hold across *many* files, are invisible
in development, and produce no error when they break —

  * a cookie-first CSRF reader works in dev (`DEBUG=True`, readable cookie) and
    silently 403s every POST in testing/production;
  * a `.cancel-armed` class no stylesheet defines just renders nothing;
  * a `bg-primary-500` on a page that loads Tailwind without the palette emits
    no CSS at all;
  * `html.dark` overrides only fail on a screen somebody is looking at;
  * an `aria-label` nobody adds is only missing for users nobody tests as.

A rendering test can only ever cover the one page it renders, so it would let
the *next* page reintroduce the same fault. These read the whole tree, the way
the pre-commit static-analysis hooks do.

None of them touch the database, so they need no fixtures and no
`django_db` marker.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[2]
CORE_TEMPLATES = PROJECT_DIR / "core" / "templates"
ROOT_TEMPLATES = PROJECT_DIR / "templates"
JS_DIR = PROJECT_DIR / "core" / "static" / "js"
CSS_DIR = PROJECT_DIR / "core" / "static" / "css"


def _templates() -> list[Path]:
    """Every project template, app-level and project-level."""
    found = sorted(CORE_TEMPLATES.rglob("*.html")) + sorted(ROOT_TEMPLATES.rglob("*.html"))
    assert found, "no templates found — did the tree move?"
    return found


def _app_js() -> list[Path]:
    """Our own JS modules (the vendored Tailwind build is not ours to lint)."""
    found = [p for p in sorted(JS_DIR.rglob("*.js")) if "vendor" not in p.parts]
    assert found, "no JS modules found — did the tree move?"
    return found


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return path.relative_to(PROJECT_DIR).as_posix()


def _blank(match: re.Match[str]) -> str:
    """Replace a match with the same number of characters.

    Keeps every later offset and line number intact, so a comment can be
    excluded from a scan without shifting the positions it reports.
    """
    return re.sub(r"[^\n]", " ", match.group(0))


def _without_comments(source: str) -> str:
    """Blank out Django, HTML and JS/CSS comments.

    Every scan below has to ignore commentary, because the commentary in this
    codebase *quotes the bug it is documenting* — the note next to
    `window.localDateISO` contains the literal `toISOString().split('T')[0]`,
    the one in testing_tools.html quotes the weak escaper it replaced. Without
    this, each fix would trip its own test.
    """
    source = re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", _blank, source, flags=re.S)
    source = re.sub(r"\{#.*?#\}", _blank, source, flags=re.S)
    source = re.sub(r"<!--.*?-->", _blank, source, flags=re.S)
    source = re.sub(r"/\*.*?\*/", _blank, source, flags=re.S)
    source = re.sub(r"^[ \t]*//[^\n]*", _blank, source, flags=re.M)
    return source


# ───────────────────────────────────────────────────────────────────────────────
# Django template syntax traps
# ───────────────────────────────────────────────────────────────────────────────


def test_no_multiline_django_hash_comments():
    """`{# ... #}` is single-line only; unclosed, it renders as visible text.

    The lexer's regex does not match across newlines, so a `{#` with no `#}` on
    the same line is not a comment at all — the text is emitted into the page.
    This broke the UI in six templates once already. Multi-line commentary must
    use a `{% comment %}` block.
    """
    offenders = []
    for path in _templates():
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            # An opening `{#` whose line never closes it.
            for match in re.finditer(r"\{#", line):
                if "#}" not in line[match.end() :]:
                    offenders.append(f"{_rel(path)}:{lineno}")
    assert not offenders, (
        f"multi-line {{# #}} comments render as literal visible text; use {{% comment %}} instead: {offenders}"
    )


def test_messages_are_rendered_once():
    """Only standalone shells may loop `messages` — base.html owns the block.

    base.html renders the flash messages for every authenticated page. A second
    loop in a template that extends it consumes the same message twice. The
    standalone shells (login, the parent-portal layout, the 2FA page, the
    password-reset layout) do NOT extend base.html and legitimately keep their
    own loop.
    """
    offenders = []
    for path in _templates():
        source = _read(path)
        if not re.search(r"\{%\s*for\s+\w+\s+in\s+messages\s*%\}", source):
            continue
        if path.name == "base.html" and path.parent == CORE_TEMPLATES:
            continue
        if re.search(r"""\{%\s*extends\s+["']base\.html["']\s*%\}""", source):
            offenders.append(_rel(path))
    assert not offenders, (
        f"these templates extend base.html AND loop `messages`, so each message is consumed twice: {offenders}"
    )


# ───────────────────────────────────────────────────────────────────────────────
# Localisation
# ───────────────────────────────────────────────────────────────────────────────


def test_every_html_element_declares_spanish():
    """The whole UI (and every email) is Spanish, so `lang` must say so.

    base.html and emails/base_email.html — the two widest-cascading templates in
    the project — both shipped `lang="en"`, which makes a screen reader
    pronounce every page with English phonetics and makes the browser offer to
    translate the academy's own mail.
    """
    offenders = []
    for path in _templates():
        for match in re.finditer(r"<html\b[^>]*>", _read(path)):
            tag = match.group(0)
            lang = re.search(r'lang="([^"]*)"', tag)
            if lang is None or not lang.group(1).lower().startswith("es"):
                offenders.append(f"{_rel(path)} -> {tag}")
    assert not offenders, f'<html> without lang="es": {offenders}'


# ───────────────────────────────────────────────────────────────────────────────
# CSRF: hidden input first, cookie only as a fallback
# ───────────────────────────────────────────────────────────────────────────────

# The only two places allowed to read the csrftoken COOKIE at all.
#   base.js                     — builds window.CSRF_TOKEN for the whole app.
#   parent_portal/payments.html — the portal shell does not load base.js.
COOKIE_READER_ALLOWLIST = {
    "core/static/js/base.js",
    "core/templates/parent_portal/payments.html",
}


def test_csrf_token_is_read_from_the_hidden_input_not_the_cookie():
    """`CSRF_COOKIE_HTTPONLY` is True whenever DEBUG=False.

    So `document.cookie` carries no csrftoken in testing or production and a
    cookie-first reader silently 403s every POST there while working perfectly
    in development. testing_tools.html had exactly that inversion — on the one
    page that only ever runs in the testing environment.

    Two rules, both checked: almost nothing may read the cookie (everything
    routes through `window.CSRF_TOKEN`), and the two places that may must read
    the hidden input FIRST.
    """
    cookie_readers = []
    inverted = []
    for path in _app_js() + _templates():
        source = _read(path)
        cookie_at = source.find("csrftoken=")
        if cookie_at == -1:
            continue
        rel = _rel(path)
        if rel not in COOKIE_READER_ALLOWLIST:
            cookie_readers.append(rel)
            continue
        input_at = source.find("csrfmiddlewaretoken")
        assert input_at != -1, f"{rel} reads the cookie and never the hidden input"
        if input_at > cookie_at:
            inverted.append(rel)

    assert not cookie_readers, (
        "these read the csrftoken cookie instead of window.CSRF_TOKEN, which is "
        f"empty whenever DEBUG=False: {cookie_readers}"
    )
    assert not inverted, f"cookie read BEFORE the hidden input (403s in testing/prod): {inverted}"


# ───────────────────────────────────────────────────────────────────────────────
# One primitive per job: escaping, local dates, response checking
# ───────────────────────────────────────────────────────────────────────────────


def test_shared_escape_helper_escapes_both_quote_characters():
    """`window.escapeHtml` is the app's single escaping primitive.

    testing_tools.html carried a second, weaker one (`div.textContent = s;
    return div.innerHTML`) which does NOT escape quotes — and used it in an
    attribute position, where a value containing `"` closes the attribute and
    injects markup.
    """
    base_js = _read(JS_DIR / "base.js")
    for needle in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert needle in base_js, f"window.escapeHtml no longer emits {needle}"

    offenders = []
    for path in _app_js() + _templates():
        if path.name == "base.js":
            continue
        source = _without_comments(_read(path))
        # The exact shape of the weak escaper: textContent in, innerHTML out.
        if re.search(r"textContent\s*=\s*\w+;?\s*return\s+\w+\.innerHTML", source):
            offenders.append(_rel(path))
    assert not offenders, (
        f"second escapeHtml implementation that does not escape quotes; use window.escapeHtml: {offenders}"
    )


def test_no_utc_date_used_as_a_local_calendar_date():
    """`new Date().toISOString().split('T')[0]` is the UTC date, not today.

    Madrid is UTC+1/+2, so between local midnight and 01:00/02:00 it returns
    YESTERDAY. home.js used it for both the "Hoy" todo due date and the Friday
    offset; payments.js already had the correct `localDateISO` but kept it
    file-private. It now lives in base.js and both read it from there.
    """
    offenders = []
    for path in _app_js() + _templates():
        source = _without_comments(_read(path))
        if re.search(r"toISOString\(\)\s*\.\s*split\(\s*['\"]T['\"]\s*\)", source):
            offenders.append(_rel(path))
    assert not offenders, (
        "toISOString() is the UTC date and is off by one for hours of every "
        f"local day; use window.localDateISO(): {offenders}"
    )

    base_js = _read(JS_DIR / "base.js")
    assert "window.localDateISO" in base_js, "base.js must export the localDateISO primitive"
    assert "window.localDateISO" in _read(JS_DIR / "home.js")
    assert "window.localDateISO" in _read(JS_DIR / "payments.js")


def test_base_js_exports_the_shared_fetch_helpers():
    """The status-checking helpers exist and answer in Spanish.

    17 of ~21 fetch sites never checked the HTTP status, so an expired session
    (403, or a redirect to /login/ returning HTML that JSON.parse chokes on)
    reached the user as "Error de conexión" — wrong, and unactionable.
    """
    base_js = _read(JS_DIR / "base.js")
    for symbol in ("window.apiFetch", "window.apiErrorMessage", "window.API_MESSAGES"):
        assert symbol in base_js, f"base.js must export {symbol}"
    assert "sesión ha caducado" in base_js, "the 401/403 message must name the expired session"
    assert "Demasiados intentos" in base_js, "429 must be distinguishable from a server error"

    # Every export must have a reader. `window.CSRF_TOKEN` was exported and read
    # by nothing while six modules kept their own copy of the reader — that is
    # the state this whole change exists to undo, so it is worth pinning.
    readers = "".join(_read(p) for p in _app_js() if p.name != "base.js")
    readers += "".join(_read(p) for p in _templates())
    for symbol in ("window.apiFetch", "window.apiErrorMessage", "window.CSRF_TOKEN", "window.escapeHtml"):
        assert symbol in readers, f"{symbol} is exported by base.js and read by nothing"


# ───────────────────────────────────────────────────────────────────────────────
# CSS invariants
# ───────────────────────────────────────────────────────────────────────────────


def test_cancel_armed_state_is_styled_in_both_themes():
    """payments.js toggles `.cancel-armed`; no stylesheet used to define it.

    The armed half of the two-click cancel was therefore invisible — the label
    changed to "¿Seguro?" inside a button still styled as an ordinary grey icon
    — and a second mis-click soft-deletes a payment with no undo outside
    /admin/.
    """
    assert "cancel-armed" in _read(JS_DIR / "payments.js"), "payments.js no longer arms the button"
    assert ".cancel-armed" in _read(CSS_DIR / "app.css"), "no light-mode .cancel-armed rule"
    assert "html.dark .cancel-armed" in _read(CSS_DIR / "theme.css"), "no html.dark override"


def test_payments_js_addresses_cells_by_name_not_column_index():
    """`row.querySelectorAll('td')[5]` breaks silently on any column change.

    The quick-complete handler would rewrite whichever column happened to be
    fifth with a status badge, and nothing would error. The cells now carry
    `data-cell="status|method|payment-date"`.
    """
    payments_js = _read(JS_DIR / "payments.js")
    assert not re.search(r"querySelectorAll\(\s*['\"]td['\"]\s*\)\s*\[", payments_js), (
        "payments.js is indexing table cells positionally again"
    )
    payments_list = _read(CORE_TEMPLATES / "payments" / "payments_list.html")
    for name in ("status", "method", "payment-date"):
        assert f'data-cell="{name}"' in payments_list, f"payments_list.html lost data-cell={name}"
        assert f"cell(row, '{name}')" in payments_js, f"payments.js no longer looks up the {name} cell by name"


def test_no_dead_sidebar_expanded_css():
    """The `.sidebar-expanded` block was unreachable — nothing added the class.

    Its only observable consequence was that `.sidebar-session` ("Sesión
    activa") never rendered anywhere, because that was the sole selector
    revealing it. The dead half is deleted and `.sidebar-session` is folded into
    the live `expandable-sidebar:hover` group.
    """
    app_css = _without_comments(_read(CSS_DIR / "app.css"))
    assert "sidebar-expanded" not in app_css, "the dead .sidebar-expanded rules are back"
    assert "expandable-sidebar:hover .sidebar-session" in app_css, (
        "'Sesión activa' is only revealed by the hover group; without it the block in base.html renders nowhere"
    )
    for path in _templates():
        assert "sidebar-expanded" not in _without_comments(_read(path)), (
            f"{_rel(path)} references the removed sidebar-expanded state"
        )


# The closed set of inline light background hexes that theme.css rewrites for
# dark mode. See the comment block above those selectors — this list and that
# one are the same list, and this test is what keeps them so.
MATCHED_INLINE_BACKGROUND_HEXES = {
    "#fff",
    "#ffffff",
    "#f5f5f5",
    "#fafafa",
    "#f9fafb",
    "#f8fafc",
    "#f0f0f0",
    "#fef3c7",
    "#fef2f2",
    "#fefce8",
    "#fdf2f8",
    "#f0fdf4",
    "#ede9fe",
    "#ddd6fe",
    "#f5f3ff",
    "#faf5ff",
    "#f3e8ff",
    # Semantic status surfaces — mapped to the dark status colours, not to the
    # neutral surface, so a red pill stays red.
    "#fee2e2",
    "#dcfce7",
    "#fffbeb",
}


def _is_light(hex_value: str) -> bool:
    """Rough perceived lightness — enough to tell a light surface from a dark one."""
    raw = hex_value.lstrip("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    r, g, b = (int(raw[i : i + 2], 16) for i in (0, 2, 4))
    return (0.299 * r + 0.587 * g + 0.114 * b) > 150


def test_no_unmatched_inline_light_background_hex():
    """theme.css rewrites inline light backgrounds by SUBSTRING-matching the attribute.

    That is the structural cost of having no build step: a template that writes
    `style="background:#f4f4f5"` — one shade away from a matched value — renders
    white-on-dark in dark mode and nothing detects it. This test is the
    detector. If it fails, either add the hex to the matching group in theme.css
    (and to the set above) or, better, move the colour onto a utility class.
    """
    # ONLY inline `style="…"` attributes: a hex inside a <style> block is an
    # ordinary CSS rule that a class selector in theme.css can override, which
    # is how qa/_qa_styles.html's palette is handled.
    attribute = re.compile(r"""\bstyle\s*=\s*"([^"]*)\"""")
    pattern = re.compile(r"background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,8})")
    offenders = []
    for path in _templates():
        # Emails are deliberately dark-only and never load theme.css.
        if "emails" in path.parts:
            continue
        source = _without_comments(_read(path))
        for attr in attribute.finditer(source):
            lineno = source[: attr.start()].count("\n") + 1
            for match in pattern.finditer(attr.group(1)):
                value = match.group(1).lower()
                if value in MATCHED_INLINE_BACKGROUND_HEXES:
                    continue
                if not _is_light(value):
                    continue  # already dark — dark mode needs no override
                offenders.append(f"{_rel(path)}:{lineno} {value}")
    assert not offenders, (
        "inline LIGHT background hex with no html.dark override in theme.css — "
        f"it will render as a white card on a dark page: {offenders}"
    )


# `bg-*-600/700` on the QA pages are the solid emerald / rose action buttons.
# They must stay vivid on dark, so they deliberately have no override.
DARK_OVERRIDE_EXEMPT = {
    "bg-emerald-600",
    "bg-emerald-700",
    "bg-rose-600",
    "bg-rose-700",
}

QA_TEMPLATES = (
    "testing_tools.html",
    "features.html",
    "feature_detail.html",
    "qa/_qa_styles.html",
)

TAILWIND_COLOR_NAMES = (
    "slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|"
    "teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose"
)


def test_qa_dashboard_utilities_have_dark_overrides():
    """The QA pages reach outside the palettes the rest of the app uses.

    They use indigo, violet, rose, cyan and the `gray-*` scale rather than
    `neutral-*`, and those had no `html.dark` rule at all: the round icon chips
    behind every card heading stayed LIGHT indigo/violet on the dark surface and
    `border-gray-300` on the backlog inputs stayed near-white. The convention
    (CLAUDE.md) is that a new surface/text utility gets its override in the same
    change; this is that convention made checkable.
    """
    theme_css = _read(CSS_DIR / "theme.css")
    covered = set(re.findall(r"html\.dark \.((?:bg|text|border)-[a-z]+-\d+)", theme_css))

    used_pattern = re.compile(rf"(?:bg|text|border)-(?:{TAILWIND_COLOR_NAMES})-\d+")
    missing: dict[str, set[str]] = {}
    for name in QA_TEMPLATES:
        source = _read(CORE_TEMPLATES / name)
        for utility in used_pattern.findall(source):
            if utility in covered or utility in DARK_OVERRIDE_EXEMPT:
                continue
            missing.setdefault(utility, set()).add(name)
    assert not missing, (
        "QA utilities with no html.dark override in theme.css — they render "
        f"light-on-dark: { {k: sorted(v) for k, v in missing.items()} }"
    )


# ───────────────────────────────────────────────────────────────────────────────
# One violet palette
# ───────────────────────────────────────────────────────────────────────────────


def test_violet_palette_has_a_single_source_per_language():
    """The `primary` scale was pasted into four places and had already drifted.

    two_factor/verify.html's copy was missing the `fontFamily` override the
    other two carried. There is deliberately no build step, so the palette
    cannot literally exist once — hand-written CSS cannot read a JS object — but
    it now exists exactly twice, in one file each, and this test keeps the two
    in agreement.
    """
    js_shades = dict(re.findall(r"(\d{2,3}):\s*'(#[0-9a-f]{6})'", _read(JS_DIR / "tailwind-config.js")))
    css_shades = dict(re.findall(r"--primary-(\d{2,3}):\s*(#[0-9a-f]{6});", _read(CSS_DIR / "palette.css")))
    assert len(js_shades) == 10, f"tailwind-config.js should define 10 shades, found {js_shades}"
    assert js_shades == css_shades, (
        f"js/tailwind-config.js and css/palette.css disagree about the violet palette: JS={js_shades} CSS={css_shades}"
    )


def test_no_template_declares_its_own_tailwind_config():
    """Every Tailwind-loading page must use the shared config file.

    A page that loads the vendored Play build WITHOUT the palette renders
    `bg-primary-500` as nothing at all — no error, no fallback. That is how the
    parent portal's "Certificado fiscal" button became white text on a white
    card. Sharing one file removes both the drift and the chance of forgetting.
    """
    offenders = []
    loaders = []
    for path in _templates():
        source = _read(path)
        if "tailwind.config" in source:
            offenders.append(_rel(path))
        if "tailwindcss-play" in source:
            loaders.append((path, source))
    assert not offenders, f"inline tailwind.config block — use js/tailwind-config.js: {offenders}"
    assert loaders, "no template loads the vendored Tailwind build — did it move?"
    for path, source in loaders:
        assert "js/tailwind-config.js" in source, (
            f"{_rel(path)} loads Tailwind without the palette; every primary-* utility on it will emit no CSS"
        )
        assert "css/palette.css" in source, (
            f"{_rel(path)} loads theme.css rules that read var(--primary-*) "
            "without palette.css; an undefined var() computes to the initial "
            "value, which renders the violet buttons transparent"
        )


# ───────────────────────────────────────────────────────────────────────────────
# Admin gating in templates
# ───────────────────────────────────────────────────────────────────────────────


def _admin_gated_spans(source: str) -> list[tuple[int, int]]:
    """Character ranges that sit inside an `{% if is_admin_user %}` branch.

    A small nesting-aware `{% if %}` / `{% elif %}` / `{% else %}` / `{% endif %}`
    walker. Character ranges, not line numbers, because several of these gates
    open and close on ONE line (`{% if is_admin_user %}<td>…</td>{% endif %}`) —
    a per-line tracker reads that as ungated.

    It exists at all because the failure mode is a control rendered OUTSIDE the
    gate, and asserting on a rendered response only ever covers the one page and
    the one role the test happens to exercise.
    """
    spans: list[tuple[int, int]] = []
    stack: list[list] = []  # [is_admin_branch, branch_start_offset]

    def is_admin_condition(rest: str) -> bool:
        return "is_admin_user" in rest and "not is_admin_user" not in rest

    for match in re.finditer(r"\{%\s*(if|elif|else|endif)\b([^%]*?)%\}", source):
        keyword, rest = match.group(1), match.group(2)
        if keyword == "if":
            stack.append([is_admin_condition(rest), match.end()])
        elif not stack:
            continue
        elif keyword in ("elif", "else"):
            if stack[-1][0]:
                spans.append((stack[-1][1], match.start()))
            stack[-1] = [keyword == "elif" and is_admin_condition(rest), match.end()]
        else:  # endif
            gated, start = stack.pop()
            if gated:
                spans.append((start, match.start()))
    return spans


@pytest.mark.parametrize(
    ("template", "needles"),
    [
        # /expenses/ write endpoints. `update_expense` and `delete_expense` are
        # NOT in NON_ADMIN_ALLOWED_URL_NAMES, so for a non-admin teacher these
        # controls could only ever bounce to home with a permission flash — a
        # dead button that reads as a broken feature. expenses.html had ZERO
        # references to is_admin_user.
        ("expenses.html", ("delete_expense", "update_expense", 'id="expense-edit-modal"')),
        # The student ficha stays reachable by a non-admin teacher (they need
        # the parent's phone number) but every figure on it is money.
        ("student_detail.html", ("student_payments_pdf", "enrollment.final_amount", "modality-toggle-btn")),
    ],
)
def test_financial_write_controls_are_admin_gated(template, needles):
    path = CORE_TEMPLATES / template
    source = _without_comments(_read(path))
    spans = _admin_gated_spans(source)
    assert spans, f"{_rel(path)} has no {{% if is_admin_user %}} gate at all"
    offenders = []
    for needle in needles:
        for match in re.finditer(re.escape(needle), source):
            if any(start <= match.start() < end for start, end in spans):
                continue
            lineno = source[: match.start()].count("\n") + 1
            offenders.append(f"{_rel(path)}:{lineno} {needle}")
    assert not offenders, f"admin-only control rendered outside {{% if is_admin_user %}}: {offenders}"


def test_logout_is_a_post_form_not_a_link():
    """Both logout views are `@require_http_methods(["POST"])`.

    A GET logout is CSRF-able (any third-party `<img src="/logout/">` ends the
    session), so the views reject GET — which makes an `<a href>` a 405.
    """
    for template, url_name in (
        (CORE_TEMPLATES / "base.html", "logout"),
        (CORE_TEMPLATES / "parent_portal" / "base_portal.html", "parent_portal_logout"),
    ):
        source = _read(template)
        assert re.search(
            r"""<form[^>]*method=["']post["'][^>]*action="\{%\s*url\s+['"]""" + url_name + r"""['"]\s*%\}""",
            source,
        ), f"{_rel(template)} must POST to {url_name}"
        assert not re.search(r"""<a[^>]*href="\{%\s*url\s+['"]""" + url_name + r"""['"]\s*%\}""", source), (
            f"{_rel(template)} still links to {url_name} with GET (405)"
        )
        assert "csrf_token" in source


# ───────────────────────────────────────────────────────────────────────────────
# Accessibility
# ───────────────────────────────────────────────────────────────────────────────

ICON_CONTROL_RE = re.compile(r"<(button|a)(\s[^<>]*)>(.{0,900}?)</\1>", re.S | re.I)
ICON_SPAN_RE = re.compile(r"<span[^>]*material-symbols[^>]*>.*?</span>", re.S)


def _visible_text(inner: str) -> str:
    """Text a sighted user reads inside a control, ignoring the icon ligature.

    A Material Symbols span's *content* is the ligature name ("close", "tune"),
    which is drawn as a glyph and never read as text — so it is stripped first.
    `{{ … }}` is kept as a placeholder because it renders to real text.
    """
    text = ICON_SPAN_RE.sub(" ", inner)
    text = re.sub(r"\{\{.*?\}\}", "X", text, flags=re.S)
    text = re.sub(r"\{%.*?%\}", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"&nbsp;|\s+", " ", text).strip()


def test_icon_only_controls_have_an_accessible_name():
    """55 icon-only buttons and links had no accessible name at all.

    33 had a `title` (a weak fallback: not announced by every screen reader, and
    never on a touch device) and ~12 had neither, so they were announced as
    "button" — including every filter, every modal close and every row action.
    `title` is kept where it was; `aria-label` is what actually names them, in
    Spanish.
    """
    offenders = []
    for path in _templates():
        if "emails" in path.parts:
            continue
        source = _read(path)
        for match in ICON_CONTROL_RE.finditer(source):
            attrs, inner = match.group(2), match.group(3)
            if "material-symbols" not in inner:
                continue
            if _visible_text(inner) or "aria-label" in attrs:
                continue
            lineno = source[: match.start()].count("\n") + 1
            offenders.append(f"{_rel(path)}:{lineno}")
    assert not offenders, f"icon-only control with no accessible name: {offenders}"


def test_sidebar_links_have_an_accessible_name():
    """The sidebar label span is `display:none` on 12 of 13 pages.

    `hidden sidebar-text` is revealed only by `#main-sidebar.expandable-sidebar:hover`,
    which is applied on the dashboard alone — and `display:none` text is
    excluded from the accessible name computation, so everywhere else the whole
    primary navigation was a column of unnamed links.
    """
    source = _read(CORE_TEMPLATES / "base.html")
    offenders = []
    for match in re.finditer(r"<(a|button)(\s[^<>]*class=\"sidebar-link[^<>]*)>", source):
        if "aria-label" not in match.group(2):
            lineno = source[: match.start()].count("\n") + 1
            offenders.append(f"base.html:{lineno}")
    assert not offenders, f"sidebar control with no aria-label: {offenders}"
    # Sanity: the sweep above must actually have found the nav.
    assert source.count('class="sidebar-link') >= 10


def test_modal_dialogs_get_the_shared_focus_management():
    """Focus handling is done once in base.js, not nine times in nine scripts.

    None of the nine `aria-modal` panels managed focus: opening one left the
    caret in the page behind it, Tab walked out of the dialog, closing it
    dropped focus to `<body>`, and three had no Escape handler at all —
    precisely what doing it per-modal produces. Every dialog must therefore
    carry the `role="dialog" aria-modal="true"` pair that opts it in.
    """
    base_js = _read(JS_DIR / "base.js")
    assert 'role="dialog"][aria-modal="true"]' in base_js, "base.js lost the dialog selector"
    for needle in ("Escape", "Tab", "inert", "preventScroll"):
        assert needle in base_js, f"base.js focus management lost {needle}"

    # Any fixed full-screen overlay whose id says "modal" must opt in.
    offenders = []
    for path in _templates():
        source = _read(path)
        for match in re.finditer(r"<div[^>]*id=\"([^\"]*[Mm]odal[^\"]*)\"[^>]*>", source):
            tag, element_id = match.group(0), match.group(1)
            if "inset:0" not in tag.replace(" ", "") and "inset-0" not in tag:
                continue
            if 'aria-modal="true"' in tag:
                continue
            lineno = source[: match.start()].count("\n") + 1
            offenders.append(f"{_rel(path)}:{lineno} #{element_id}")
    assert not offenders, (
        'overlay dialog without role="dialog" aria-modal="true", so it does '
        f"not inherit the shared focus trap / Escape handling: {offenders}"
    )


# ───────────────────────────────────────────────────────────────────────────────
# Optional model fields
# ───────────────────────────────────────────────────────────────────────────────


# The one site that needs no guard: the dashboard's birthday card is built from
# a queryset FILTERED on birth_date month/day, so every row it renders has one
# by construction. Listed rather than guarded so the exemption is a decision on
# the record instead of an omission.
AGE_GUARD_EXEMPT = {
    "core/templates/home.html:141",
}


def test_student_age_is_never_rendered_unguarded():
    """`Student.birth_date` is optional, so `Student.age` returns None.

    Django renders that as the literal string "None", so five templates showed
    "None años" (and the database view a bare "None") for any waiting-list entry
    taken over the phone with just a first name and a number.
    """
    offenders = []
    for path in _templates():
        source = _without_comments(_read(path))
        for match in re.finditer(r"\{\{\s*([\w.]*)\.age\s*\}\}", source):
            owner = match.group(1)
            # `{{ form.age }}` is a bound form WIDGET (waiting_list_create), not
            # the model property.
            if owner.startswith("form"):
                continue
            lineno = source[: match.start()].count("\n") + 1
            if f"{_rel(path)}:{lineno}" in AGE_GUARD_EXEMPT:
                continue
            window = source[max(0, match.start() - 300) : match.start()]
            # Either an explicit None test or a truthiness guard on the same
            # attribute. (`{% if x.age %}` also swallows a literal 0, i.e. an
            # under-one-year-old, which no student of this academy is.)
            guarded = "is not None" in window or re.search(r"\{%\s*if\s+" + re.escape(owner) + r"\.age\s*%\}", window)
            if guarded:
                continue
            offenders.append(f"{_rel(path)}:{lineno}")
    assert not offenders, (
        "`.age` rendered with no None guard — this prints the literal 'None' "
        f"for a student with no birth date: {offenders}"
    )


def test_optional_fields_do_not_claim_a_required_asterisk():
    """`birth_date` and `group` are genuinely optional on Student.

    Both carried a hardcoded red `*` in student_create.html, promising a rule
    the model does not have — and the waiting-list flow depends on being able to
    take a ficha with neither.
    """
    source = _read(CORE_TEMPLATES / "student_create.html")
    for field in ("birth_date", "group"):
        pattern = re.compile(r"\{\{\s*form\." + field + r"\.label\s*\}\}\s*<span[^>]*>\s*\*", re.S)
        assert not pattern.search(source), f"student_create.html marks the optional field `{field}` as required"


# ───────────────────────────────────────────────────────────────────────────────
# Payment list / reminder coherence
# ───────────────────────────────────────────────────────────────────────────────


def test_complete_trigger_is_hidden_on_every_uncompletable_status():
    """`quick_complete_payment` refuses cancelled AND refunded payments.

    Cancelling frees the month for `unique_pending_periodic_payment_per_month`
    so the schedule may already have re-billed it, and completing a REFUNDED
    payment rewrites `payment_date` and emails the family a receipt for money
    that was returned. `failed` stays completable — a failed card retried in
    cash is a real workflow.
    """
    source = _read(CORE_TEMPLATES / "payments" / "payments_list.html")
    match = re.search(r"\{%\s*if\s+payment\.payment_status[^%]*%\}", source)
    assert match, "the complete-trigger guard has gone"
    guard = match.group(0)
    for status in ("completed", "cancelled", "refunded"):
        assert f"'{status}'" in guard, f"the complete trigger still renders for a {status} payment: {guard}"


def test_payment_reminder_renders_a_bare_amount_plus_the_word_euros():
    """All six tarifa rows share one shape: `{{ value }} euros`.

    The Cheque Idioma row read "de 34€ euros" in the academy's most-sent parent
    email because the callers passed a value that already carried the symbol.
    The fix is on the Python side (bare numbers, as `PricingService._euros()`
    already produced for the five sibling rows), so this template must KEEP the
    literal word — and the form's placeholder must stop inviting a `€`.
    """
    email = _read(CORE_TEMPLATES / "emails" / "payment_reminder.html")
    assert "{{ reduced_price_cheque_idioma }} euros" in email, (
        "this row must match the five tarifa rows above it, which render '{{ value }} euros' from a bare number"
    )
    form = _read(CORE_TEMPLATES / "apps" / "payment_reminder_form.html")
    assert 'placeholder="34"' in form, "the placeholder must not invite a currency symbol"
    assert "Precio reducido Cheque Idioma (€)" in form, "the unit belongs on the label"


def test_backlog_rows_use_server_rendered_display_labels():
    """An AJAX-inserted backlog row showed raw keys until a reload.

    "medium" / "Open" sat next to server-rendered rows reading "Media" /
    "Abierto". The labels come from `get_priority_display()` /
    `get_status_display()` in the JSON response — never from a Spanish label map
    in JS, which would be a second copy of the model's choices and would drift
    the moment a label is renamed.
    """
    source = _read(CORE_TEMPLATES / "testing_tools.html")
    assert "priority_display" in source, "the inserted row must use the server's priority label"
    assert "status_display" in source, "the inserted row must use the server's status label"
    assert '"status-badge status-open">Open<' not in source, "raw status key back in the markup"
