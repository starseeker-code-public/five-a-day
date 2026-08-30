// Expenses page — recurrence field toggling on the create form, and the edit modal.
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


// Edit modal. The rows carry the expense in data-* attributes, so opening the
// form needs no extra request; it posts to update_expense/<id>/.
(function () {
    "use strict";

    const modal = document.getElementById("expense-edit-modal");
    const form = document.getElementById("expense-edit-form");
    if (!modal || !form) {
        return;
    }

    const isRecurring = document.getElementById("edit_is_recurring");
    const frequency = document.getElementById("edit_recurring_frequency");
    const recurringFields = document.getElementById("edit-recurring-fields");
    const freqDay = document.getElementById("edit-freq-day");
    const freqMonth = document.getElementById("edit-freq-month");
    const freqWeekdays = document.getElementById("edit-freq-weekdays");

    function show(el, visible) {
        if (el) {
            el.classList.toggle("hidden", !visible);
        }
    }

    function render() {
        const recurring = isRecurring.checked;
        show(recurringFields, recurring);
        const freq = frequency.value;
        show(freqDay, recurring && (freq === "monthly" || freq === "yearly"));
        show(freqMonth, recurring && freq === "yearly");
        show(freqWeekdays, recurring && freq === "weekly");
    }

    // Django reverses the route with a 0 placeholder ("/expenses/0/update/") and
    // JS only swaps the id in. Never hard-code a route in an external JS module —
    // see the conventions in CLAUDE.md.
    const actionTemplate = modal.dataset.updateUrlTemplate || "";

    function open(btn) {
        const d = btn.dataset;
        form.action = actionTemplate.replace("/0/update/", "/" + d.id + "/update/");
        document.getElementById("edit_description").value = d.description || "";
        document.getElementById("edit_category").value = d.category || "other";
        document.getElementById("edit_amount").value = d.amount || "";
        document.getElementById("edit_expense_date").value = d.expenseDate || "";
        document.getElementById("edit_notes").value = d.notes || "";

        isRecurring.checked = d.isRecurring === "1";
        frequency.value = d.recurringFrequency || "monthly";
        document.getElementById("edit_recurring_day").value = d.recurringDay || "";
        document.getElementById("edit_recurring_month").value = d.recurringMonth || "1";

        const days = (d.recurringWeekdays || "").split(",").filter(Boolean);
        document.querySelectorAll(".edit-weekday").forEach((cb) => {
            cb.checked = days.indexOf(cb.value) !== -1;
        });

        render();
        modal.classList.remove("hidden");
        modal.classList.add("flex");
    }

    function close() {
        modal.classList.add("hidden");
        modal.classList.remove("flex");
    }

    document.addEventListener("click", function (e) {
        const btn = e.target.closest(".expense-edit-btn");
        if (btn) {
            open(btn);
        }
    });

    isRecurring.addEventListener("change", render);
    frequency.addEventListener("change", render);
    document.getElementById("expense-edit-close").addEventListener("click", close);
    document.getElementById("expense-edit-cancel").addEventListener("click", close);
    modal.addEventListener("click", function (e) {
        if (e.target === modal) {
            close();
        }
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !modal.classList.contains("hidden")) {
            close();
        }
    });
})();
