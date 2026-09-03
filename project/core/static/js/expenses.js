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
//
// The whole modal (and the Editar / Eliminar triggers) is inside
// `{% if is_admin_user %}` in expenses.html — `update_expense` and
// `delete_expense` are not in NON_ADMIN_ALLOWED_URL_NAMES. So for a non-admin
// teacher NONE of these elements exist, and every lookup below has to be
// guarded: one unguarded addEventListener throws and takes the recurrence
// toggling on the create form (the block above) down with it.
(function () {
    "use strict";

    const modal = document.getElementById("expense-edit-modal");
    const form = document.getElementById("expense-edit-form");
    const isRecurring = document.getElementById("edit_is_recurring");
    const frequency = document.getElementById("edit_recurring_frequency");
    if (!modal || !form || !isRecurring || !frequency) {
        return;
    }

    const recurringFields = document.getElementById("edit-recurring-fields");
    const freqDay = document.getElementById("edit-freq-day");
    const freqMonth = document.getElementById("edit-freq-month");
    const freqWeekdays = document.getElementById("edit-freq-weekdays");

    function show(el, visible) {
        if (el) {
            el.classList.toggle("hidden", !visible);
        }
    }

    function setValue(id, value) {
        const el = document.getElementById(id);
        if (el) el.value = value;
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
        setValue("edit_description", d.description || "");
        setValue("edit_category", d.category || "other");
        setValue("edit_amount", d.amount || "");
        setValue("edit_expense_date", d.expenseDate || "");
        setValue("edit_notes", d.notes || "");

        isRecurring.checked = d.isRecurring === "1";
        frequency.value = d.recurringFrequency || "monthly";
        setValue("edit_recurring_day", d.recurringDay || "");
        setValue("edit_recurring_month", d.recurringMonth || "1");

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
    document.getElementById("expense-edit-close")?.addEventListener("click", close);
    document.getElementById("expense-edit-cancel")?.addEventListener("click", close);
    modal.addEventListener("click", function (e) {
        if (e.target === modal) {
            close();
        }
    });
    // Escape is also handled generically in base.js (which clicks
    // #expense-edit-close); kept here so the page still dismisses if base.js
    // fails to load.
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !modal.classList.contains("hidden")) {
            close();
        }
    });
})();
