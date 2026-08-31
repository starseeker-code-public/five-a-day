/**
 * student-create.js — Student creation form: success countdown, price calculator,
 * sibling search, parent search with pagination.
 *
 * Requires window.STUDENT_CREATE_CONFIG to be set by an inline script:
 *   window.STUDENT_CREATE_CONFIG = {
 *       studentsListUrl: '{% url "students_list" %}',
 *       priceConfig: { monthly_full: ..., monthly_part: ..., quarterly: ..., adult_group: ... },
 *       languageChequeDiscount: ...,
 *       siblingDiscount: ...,
 *       isAdultMode: true|false
 *   };
 */

// ==================== SUCCESS COUNTDOWN ====================
(function () {
    const countdownEl = document.getElementById('countdown');
    const successOverlay = document.getElementById('successOverlay');
    if (!countdownEl || !successOverlay) return;

    const cfg = window.STUDENT_CREATE_CONFIG || {};
    const studentsListUrl = cfg.studentsListUrl || '/students/';

    let sec = 4;
    const timer = setInterval(() => {
        sec--;
        if (countdownEl) countdownEl.textContent = `(${sec})`;
        if (sec <= 0) { clearInterval(timer); window.location.href = studentsListUrl; }
    }, 1000);

    successOverlay.addEventListener('click', (e) => {
        if (e.target === successOverlay) {
            clearInterval(timer);
            window.location.href = studentsListUrl;
        }
    });
})();

// ==================== PRICE CALCULATOR ====================
document.addEventListener('DOMContentLoaded', function () {
    const cfg = window.STUDENT_CREATE_CONFIG || {};
    const priceConfig = cfg.priceConfig || {
        'monthly_full': 0,
        'monthly_part': 0,
        'quarterly': 0,
        'adult_group': 0
    };
    const languageChequeFlat = cfg.languageChequeDiscount || 0;
    const siblingPct = cfg.siblingDiscount || 0;
    const isAdultMode = cfg.isAdultMode || false;

    const planSelect = document.getElementById('id_enrollment_plan');
    const specialCheckbox = document.getElementById('id_is_special');
    const manualAmountContainer = document.getElementById('manual-amount-container');
    const manualAmountInput = document.getElementById('id_manual_amount');
    // Optional hand-set matrícula. Shown with the manual price, but independent of
    // it: the recurring cuota and the one-time matrícula are two different prices.
    const specialFeeContainer = document.getElementById('special-enrollment-fee-container');
    const calculatedPrice = document.getElementById('calculated-price');
    const priceBreakdown = document.getElementById('price-breakdown');
    const lcCheckbox = document.getElementById('id_has_language_cheque');
    const siblingCheckbox = document.getElementById('id_is_sibling_discount');

    // Guard: if essential elements are missing (e.g. success page), skip
    if (!calculatedPrice) return;

    function getBasePrice() {
        if (isAdultMode) return priceConfig.adult_group;
        if (!planSelect) return 0;
        return priceConfig[planSelect.value] || 0;
    }

    // What the strike-through shows: the price BEFORE any discount. The quarterly
    // entry in priceConfig is already net of the -5%, so using it here struck through
    // the discounted total and printed the same figure twice.
    function getGrossPrice() {
        if (!isAdultMode && planSelect && planSelect.value === 'quarterly') {
            return priceConfig.quarterly_gross || getBasePrice();
        }
        return getBasePrice();
    }

    function updateCalculatedPrice() {
        if (specialCheckbox && specialCheckbox.checked) {
            manualAmountContainer.classList.remove('hidden');
            if (specialFeeContainer) specialFeeContainer.classList.remove('hidden');
            if (manualAmountInput && manualAmountInput.value) {
                calculatedPrice.textContent = parseFloat(manualAmountInput.value).toFixed(2);
            } else {
                calculatedPrice.textContent = '--';
            }
            priceBreakdown.textContent = 'precio especial';
            return;
        }
        manualAmountContainer.classList.add('hidden');
        if (specialFeeContainer) specialFeeContainer.classList.add('hidden');

        let base = getBasePrice();
        const gross = getGrossPrice();
        let breakdownParts = [];
        let final = base;

        // Quarterly already has -5% baked in
        if (planSelect && planSelect.value === 'quarterly') {
            breakdownParts.push('trimestral incl. -5%');
        }

        // Sibling discount
        if (siblingCheckbox && siblingCheckbox.checked) {
            const siblingAmount = final * (siblingPct / 100);
            final -= siblingAmount;
            breakdownParts.push(`-${siblingPct}% hermano`);
        }

        // Language cheque (flat per month, x3 for quarterly)
        if (lcCheckbox && lcCheckbox.checked) {
            let lcAmount = languageChequeFlat;
            if (planSelect && planSelect.value === 'quarterly') lcAmount *= 3;
            final -= lcAmount;
            breakdownParts.push(`-${lcAmount.toFixed(0)}\u20AC cheque`);
        }

        if (final < 0.01) final = 0.01;

        if (breakdownParts.length > 0 || final !== gross) {
            calculatedPrice.innerHTML = `<span class="line-through text-neutral-400">${gross.toFixed(2)}</span> <span class="text-green-600 font-medium">${final.toFixed(2)}</span>`;
            priceBreakdown.textContent = breakdownParts.join(', ');
        } else {
            calculatedPrice.textContent = gross.toFixed(2);
            priceBreakdown.textContent = '';
        }

        updateFirstPeriodPrice(final);
    }

    // The first period is prorated by join date: a student starting on the 15th of
    // a 30-day month pays 16/30 of it. Only the FIRST month is ever reduced, so this
    // row appears once and never applies to the recurring fee above it.
    function updateFirstPeriodPrice(final) {
        const container = document.getElementById('first-period-container');
        if (!container) return;

        const fraction = parseFloat(cfg.firstPeriodFraction);
        if (!cfg.firstPeriodIsPartial || !(fraction > 0 && fraction < 1) || !isFinite(final)) {
            container.classList.add('hidden');
            return;
        }

        // A quarter bills three months; only its first one is prorated, so the
        // period is worth (2 + fraction)/3 of a full quarter.
        const monthsInPeriod = (planSelect && planSelect.value === 'quarterly') ? 3 : 1;
        const effective = (monthsInPeriod - 1) + fraction;
        const firstAmount = final * (effective / monthsInPeriod);

        const priceEl = document.getElementById('first-period-price');
        const noteEl = document.getElementById('first-period-note');
        if (priceEl) priceEl.textContent = firstAmount.toFixed(2);
        if (noteEl) {
            const pct = Math.round(fraction * 100);
            noteEl.textContent = `${cfg.firstPeriodLabel} — ${pct}% del periodo; después ${final.toFixed(2)}€`;
        }
        container.classList.remove('hidden');
    }

    if (planSelect) planSelect.addEventListener('change', updateCalculatedPrice);
    if (specialCheckbox) specialCheckbox.addEventListener('change', updateCalculatedPrice);
    if (manualAmountInput) manualAmountInput.addEventListener('input', updateCalculatedPrice);
    if (lcCheckbox) lcCheckbox.addEventListener('change', updateCalculatedPrice);
    if (siblingCheckbox) siblingCheckbox.addEventListener('change', function() {
        const container = document.getElementById('sibling-search-container');
        if (container) container.classList.toggle('hidden', !this.checked);
        updateCalculatedPrice();
    });

    updateCalculatedPrice();

    // ==================== SIBLING SEARCH ====================
    const siblingSearch = document.getElementById('siblingSearch');
    if (siblingSearch) {
        siblingSearch.addEventListener('input', function() {
            const q = this.value.toLowerCase().trim();
            document.querySelectorAll('.sibling-option').forEach(opt => {
                opt.style.display = (q === '' || opt.dataset.search.includes(q)) ? '' : 'none';
            });
        });
        document.querySelectorAll('input[name="sibling_id"]').forEach(radio => {
            radio.addEventListener('change', function() {
                document.getElementById('id_sibling_id').value = this.value;
            });
        });
    }

    // ==================== PARENT SEARCH + PAGINATION ====================
    const parentSearchInput = document.getElementById('parentSearch');
    if (parentSearchInput) {
        const PAGE_SIZE = 6;
        const allOptions = Array.from(document.querySelectorAll('.parent-option'));
        let filteredOptions = allOptions;
        let parentPage = 1;

        function renderParentPage() {
            const isFiltered = parentSearchInput.value.trim() !== '';
            const toShow = filteredOptions;
            const totalPages = isFiltered ? 1 : Math.max(1, Math.ceil(toShow.length / PAGE_SIZE));
            if (parentPage > totalPages) parentPage = totalPages;
            const start = isFiltered ? 0 : (parentPage - 1) * PAGE_SIZE;
            const end = isFiltered ? toShow.length : start + PAGE_SIZE;

            allOptions.forEach(o => o.style.display = 'none');
            toShow.forEach((o, i) => { o.style.display = (i >= start && i < end) ? '' : 'none'; });

            const nav = document.getElementById('parentPagination');
            if (isFiltered || totalPages <= 1) { nav.innerHTML = ''; return; }
            let html = '';
            for (let i = 1; i <= totalPages; i++) {
                if (i === parentPage) {
                    html += `<span style="width:2rem;height:2rem;border-radius:9999px;display:inline-flex;align-items:center;justify-content:center;background:#8b5cf6;color:#fff;font-size:0.75rem;font-weight:700;">${i}</span>`;
                } else {
                    html += `<button type="button" class="parent-pg-btn" data-page="${i}" style="width:2rem;height:2rem;border-radius:9999px;display:inline-flex;align-items:center;justify-content:center;border:1px solid #e5e7eb;background:#fff;color:#525252;font-size:0.75rem;cursor:pointer;">${i}</button>`;
                }
            }
            nav.innerHTML = html;
            nav.querySelectorAll('.parent-pg-btn').forEach(btn => {
                btn.addEventListener('click', () => { parentPage = parseInt(btn.dataset.page); renderParentPage(); });
            });
        }

        parentSearchInput.addEventListener('input', function() {
            const q = this.value.toLowerCase().trim();
            parentPage = 1;
            filteredOptions = q === '' ? allOptions : allOptions.filter(o => o.dataset.search.includes(q));
            renderParentPage();
        });

        document.querySelectorAll('input[name="parent_id"]').forEach(radio => {
            radio.addEventListener('change', function() {
                document.getElementById('hidden_parent_id').value = this.value;
            });
        });

        renderParentPage();
    }
});
