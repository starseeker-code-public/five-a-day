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
                locale: flatpickr.l10ns && flatpickr.l10ns.es ? "es" : "default",
                // Respect min/max already declared on the element.
                minDate: el.getAttribute("min") || null,
                maxDate: el.getAttribute("max") || null,
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        enhance(document);
    });

    // Exposed so views that inject date inputs after load can re-enhance them.
    window.initDatePickers = enhance;
})();
