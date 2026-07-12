// Expenses form — toggle recurring frequency fields.
(function () {
    "use strict";

    const isRecurring = document.getElementById("is_recurring");
    const recurringFields = document.getElementById("recurring-fields");
    const frequency = document.getElementById("recurring_frequency");
    const freqDay = document.getElementById("freq-day");
    const freqMonth = document.getElementById("freq-month");
    const freqWeekdays = document.getElementById("freq-weekdays");

    if (!isRecurring || !recurringFields || !frequency) {
        return;
    }

    function show(el, visible) {
        if (el) {
            el.classList.toggle("hidden", !visible);
        }
    }

    function render() {
        const recurring = isRecurring.checked;
        show(recurringFields, recurring);

        const freq = frequency.value;
        // Day: monthly + yearly. Month: yearly only. Weekdays: weekly only.
        show(freqDay, recurring && (freq === "monthly" || freq === "yearly"));
        show(freqMonth, recurring && freq === "yearly");
        show(freqWeekdays, recurring && freq === "weekly");
    }

    isRecurring.addEventListener("change", render);
    frequency.addEventListener("change", render);
    render();
})();
