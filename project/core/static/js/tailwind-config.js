/**
 * tailwind-config.js — THE definition of the app's Tailwind theme.
 *
 * Load it immediately after the vendored Play build:
 *
 *     <script src="{% static 'js/vendor/tailwindcss-play-3.4.17.js' %}"></script>
 *     <script src="{% static 'js/tailwind-config.js' %}"></script>
 *
 * WHY A FILE AND NOT AN INLINE BLOCK
 * The violet `primary` scale was pasted into three separate inline
 * `tailwind.config` blocks — base.html, parent_portal/base_portal.html and
 * two_factor/verify.html — because the Play build reads its config from the
 * page and there is no build step to share one. They had already drifted:
 * verify.html was missing the `fontFamily` override the other two carry, so
 * that page rendered in the browser default instead of Trebuchet MS. A plain
 * static script is shared identically by all three, needs no CSP nonce (it is
 * same-origin `src`, not inline), and is content-hashed like every other asset.
 *
 * WHY IT MUST BE A CLASSIC, NON-DEFERRED SCRIPT TAG
 * The Play build installs a setter on `tailwind.config` and rebuilds when it is
 * assigned. A classic `<script src>` in `<head>` executes in document order,
 * before the body is parsed — the same timing the inline blocks had. Do NOT add
 * `defer`/`async`: the assignment would land after first paint and every
 * `primary-*` utility would flash unstyled.
 *
 * A PAGE THAT LOADS TAILWIND WITHOUT THIS FILE RENDERS VIOLET AS NOTHING.
 * `bg-primary-500` is not a Tailwind default; without the palette it is an
 * unknown utility and Tailwind emits no rule at all — no error, no fallback.
 * That is how the parent portal's "Certificado fiscal" button ended up as white
 * text on a white card: present in the DOM, invisible on screen.
 *
 * KEEP IN STEP WITH css/app.css
 * Hand-written CSS cannot read a JS object, so app.css declares the same ten
 * shades once as `:root { --primary-*: … }` and app.css/theme.css consume those
 * variables. Two definitions total — this one for the utilities Tailwind
 * generates, that one for hand-written rules. Collapsing them to literally one
 * would need either a build step (explicitly out of scope for this project) or
 * injecting the custom properties from JS, which would leave hand-written
 * colours unstyled until this file executes. `tests/integration/
 * test_frontend_template_fixes.py` asserts the two lists agree.
 */
tailwind.config = {
    darkMode: 'class',
    theme: {
        extend: {
            colors: {
                primary: {
                    50: '#f5f3ff', 100: '#ede9fe', 200: '#ddd6fe', 300: '#c4b5fd',
                    400: '#a78bfa', 500: '#8b5cf6', 600: '#7c3aed', 700: '#6d28d9',
                    800: '#5b21b6', 900: '#4c1d95',
                },
            },
            fontFamily: {
                sans: ['Trebuchet MS', 'ui-sans-serif', 'system-ui', 'sans-serif'],
            },
        },
    },
};
