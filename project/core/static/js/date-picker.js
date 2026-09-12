/*
 * Consistent dd/mm/aaaa date inputs across the whole app.
 *
 * A native <input type="date"> renders in the browser/OS locale, so on an
 * English machine it shows mm/dd/yyyy no matter what the page does — QA hit this
 * on /students/create/. flatpickr (vendored, no CDN) replaces each date input
 * with a Spanish calendar whose VISIBLE value is d/m/Y, while keeping the real
 * submitted value in Y-m-d via altInput — so Django still receives ISO dates and
 * nothing server-side changes.
 *
 * Idempotent and re-runnable: `window.initDatePickers(root)` can be called after
 * injecting markup (a modal, an AJAX panel) to enhance any new date inputs.
 *
 * IMPORTANT for callers: once enhanced, the original <input> is type=hidden and
 * what the user sees is flatpickr's separate altInput. Writing `el.value` or
 * calling `form.reset()` therefore updates the submitted value while leaving the
 * VISIBLE field showing something else — a form that displays one date and posts
 * another. Use `window.setDateValue(el, iso)` to write one, and
 * `window.dateInputElement(el)` when you need the element the user actually sees
 * (to show/hide or focus it). Form resets are re-synced automatically below.
 */
(function () {
    "use strict";

    if (typeof flatpickr === "undefined") {
        return; // vendored script missing — leave the native input untouched.
    }

    // Spanish month/day names + Monday-first weeks, if the locale bundle loaded.
    if (flatpickr.l10ns && flatpickr.l10ns.es) {
        flatpickr.localize(flatpickr.l10ns.es);
    }

    function enhance(root) {
        var scope = root || document;
        var inputs = scope.querySelectorAll('input[type="date"]:not([data-fp-done])');
        inputs.forEach(function (el) {
            el.setAttribute("data-fp-done", "1");
            flatpickr(el, {
                // Real (submitted) value stays ISO so Django parses it unchanged.
                dateFormat: "Y-m-d",
                // What the family/admin sees and can type.
                altInput: true,
                altFormat: "d/m/Y",
                allowInput: true,
                // No `locale:` here — `flatpickr.localize()` above already made
                // Spanish the default for every instance, and the fallback value
                // would have been "default" anyway.
                // Respect min/max already declared on the element.
                minDate: el.getAttribute("min") || null,
                maxDate: el.getAttribute("max") || null,
            });
        });
    }

    function resolve(el) {
        return typeof el === "string" ? document.getElementById(el) : el;
    }

    /**
     * The element the user actually sees for a date input — flatpickr's altInput
     * once enhanced, the input itself otherwise. Use it to show/hide or focus a
     * date field: the original is type=hidden after enhancement, so toggling a
     * `hidden` class on it (or focusing it) has no visible effect at all.
     */
    window.dateInputElement = function (el) {
        el = resolve(el);
        return (el && el._flatpickr && el._flatpickr.altInput) || el;
    };

    /**
     * Set a date input's value so the visible field and the submitted value
     * agree. `value` is an ISO "YYYY-MM-DD" string (or "" to clear).
     *
     * A plain `el.value = iso` writes only the hidden original, leaving the
     * visible field blank or — worse — showing the date from whatever record was
     * opened before, which is a form that displays one date and posts another.
     */
    window.setDateValue = function (el, value) {
        el = resolve(el);
        if (!el) return;
        if (el._flatpickr) {
            // `false` = don't fire onChange; this is a programmatic fill, not a
            // user edit, and listeners react to the latter.
            el._flatpickr.setDate(value || null, false);
        } else {
            el.value = value || "";
        }
    };

    // `form.reset()` restores the original inputs' defaultValue but cannot know
    // about flatpickr's altInput, so a reused modal (enroll, new todo) kept
    // showing the previously picked date while submitting the default one.
    // `reset` bubbles, so one listener covers every form on the page.
    document.addEventListener("reset", function (event) {
        var form = event.target;
        if (!form || typeof form.querySelectorAll !== "function") return;
        // After the browser has applied the reset.
        setTimeout(function () {
            form.querySelectorAll("input[data-fp-done]").forEach(function (el) {
                if (el._flatpickr) el._flatpickr.setDate(el.value || null, false);
            });
        }, 0);
    });

    document.addEventListener("DOMContentLoaded", function () {
        enhance(document);
    });

    // Exposed so views that inject date inputs after load can re-enhance them.
    window.initDatePickers = enhance;
})();
