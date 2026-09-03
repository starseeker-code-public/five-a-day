(function () {
    const toggleBtn   = document.getElementById('emailPreviewToggleBtn');
    const toggleIcon  = document.getElementById('emailPreviewToggleIcon');
    const toggleLabel = document.getElementById('emailPreviewToggleLabel');
    const panel       = document.getElementById('emailPreviewPanel');
    const spinner     = document.getElementById('emailPreviewSpinner');
    const bodyEl      = document.getElementById('emailPreviewBody');
    const refreshBtn  = document.getElementById('emailPreviewRefreshBtn');
    const testSendBtn = document.getElementById('previewTestSendBtn');
    const feedback    = document.getElementById('previewTestFeedback');

    function getForm() {
        return toggleBtn ? (toggleBtn.closest('form') || document.querySelector('form')) : document.querySelector('form');
    }

    function fetchPreview() {
        let form = getForm();
        if (!form) return;
        let data = new FormData(form);
        data.set('action', 'preview');

        bodyEl.classList.add('hidden');
        spinner.classList.remove('hidden');

        fetch(window.location.pathname, {
            method: 'POST',
            headers: { 'X-CSRFToken': data.get('csrfmiddlewaretoken') },
            body: data,
        })
        .then(function (r) { return r.json(); })
        .then(function (d) {
            spinner.classList.add('hidden');
            bodyEl.classList.remove('hidden');
            /* bodyEl is a sandboxed <iframe>: the email is a full document whose
               stylesheets would leak onto the app page if injected via innerHTML. */
            if (d.html) bodyEl.srcdoc = d.html;
        })
        .catch(function () {
            spinner.classList.add('hidden');
            bodyEl.classList.remove('hidden');
        });
    }

    if (toggleBtn) {
        toggleBtn.addEventListener('click', function () {
            const isHidden = panel.classList.toggle('hidden');
            if (isHidden) {
                toggleIcon.textContent  = 'visibility';
                toggleLabel.textContent = 'Ver vista previa del email';
            } else {
                toggleIcon.textContent  = 'visibility_off';
                toggleLabel.textContent = 'Ocultar vista previa';
                fetchPreview();
            }
        });
    }

    if (refreshBtn) {
        refreshBtn.addEventListener('click', fetchPreview);
    }

    if (testSendBtn) {
        testSendBtn.addEventListener('click', function () {
            let form = getForm();
            if (!form) return;
            let data = new FormData(form);
            data.set('action', 'test_send');

            testSendBtn.disabled = true;
            testSendBtn.innerHTML = '<span class="material-symbols-outlined">sync</span> Enviando…';
            feedback.classList.add('hidden');

            fetch(window.location.pathname, {
                method: 'POST',
                headers: { 'X-CSRFToken': data.get('csrfmiddlewaretoken') },
                body: data,
            })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                feedback.className = d.ok
                    ? 'mb-3 p-3 rounded-lg text-sm font-medium border bg-green-50 text-green-800 border-green-200'
                    : 'mb-3 p-3 rounded-lg text-sm font-medium border bg-red-50 text-red-800 border-red-200';
                feedback.textContent = d.message || (d.ok ? '✅ Enviado' : '❌ Error');
                feedback.classList.remove('hidden');
                testSendBtn.disabled = false;
                testSendBtn.innerHTML = '<span class="material-symbols-outlined">science</span> Enviar prueba';
            })
            .catch(function () {
                feedback.className = 'mb-3 p-3 rounded-lg text-sm font-medium border bg-red-50 text-red-800 border-red-200';
                feedback.textContent = '❌ Error de conexión';
                feedback.classList.remove('hidden');
                testSendBtn.disabled = false;
                testSendBtn.innerHTML = '<span class="material-symbols-outlined">science</span> Enviar prueba';
            });
        });
    }
}());

/**
 * Generic form submit handler for app forms.
 * Add data-confirm="message" to a form to get a confirmation dialog.
 * The submit button inside the form gets disabled + spinner on submit.
 */
(function () {
    document.querySelectorAll('form[data-confirm]').forEach(function (form) {
        form.addEventListener('submit', function (e) {
            const msg = form.dataset.confirm;
            if (msg && !confirm(msg)) {
                e.preventDefault();
                return;
            }
            const btn = form.querySelector('button[type="submit"], input[type="submit"]');
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<span class="material-symbols-outlined animate-spin mr-2">sync</span>Enviando...';
            }
        });
    });

    /* Form-specific: receipt type toggle */
    const receiptTypeSelect = document.getElementById('receiptType');
    if (receiptTypeSelect) {
        const childFields = document.getElementById('quarterly-child-fields');
        const adultFields = document.getElementById('adult-fields');
        receiptTypeSelect.addEventListener('change', function () {
            if (childFields) childFields.classList.toggle('hidden', this.value !== 'quarterly_child');
            if (adultFields) adultFields.classList.toggle('hidden', this.value !== 'monthly_adult');
        });
    }

    /* Form-specific: enrollment email type toggle */
    const emailTypeSelect = document.getElementById('emailTypeSelect');
    if (emailTypeSelect) {
        const welcomeFields = document.getElementById('welcome-fields');
        const enrollmentFields = document.getElementById('enrollment-fields');
        emailTypeSelect.addEventListener('change', function () {
            if (welcomeFields) welcomeFields.classList.toggle('hidden', this.value !== 'welcome');
            if (enrollmentFields) enrollmentFields.classList.toggle('hidden', this.value !== 'enrollment');
        });
    }
}());

/* ── Enrollment-form tabs (apps/enrollment_form.html) ─────────────────────────
   The template shipped with onclick="switchTab(...)" and NO script defining it:
   both tab buttons threw ReferenceError and the "Confirmación de Matrícula" tab
   was unreachable since the page was created. Wired here with listeners so the
   template carries no inline handlers (CSP), guarded so this block no-ops on
   every other page that loads this file. */
(function () {
    const tabWelcome = document.getElementById('tab-welcome');
    const tabEnrollment = document.getElementById('tab-enrollment');
    if (!tabWelcome || !tabEnrollment) return;

    const emailType = document.getElementById('email_type');
    const infoWelcome = document.getElementById('info-welcome');
    const infoEnrollment = document.getElementById('info-enrollment');
    const enrollmentFields = document.getElementById('enrollment-fields');

    const ACTIVE = ['border-primary-500', 'text-primary-600'];
    const INACTIVE = ['border-transparent', 'text-neutral-500'];

    function paint(btn, active) {
        btn.classList.remove(...(active ? INACTIVE : ACTIVE));
        btn.classList.add(...(active ? ACTIVE : INACTIVE));
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
    }

    function switchTab(which) {
        const enrollment = which === 'enrollment';
        if (emailType) emailType.value = which;
        paint(tabWelcome, !enrollment);
        paint(tabEnrollment, enrollment);
        if (infoWelcome) infoWelcome.classList.toggle('hidden', enrollment);
        if (infoEnrollment) infoEnrollment.classList.toggle('hidden', !enrollment);
        if (enrollmentFields) enrollmentFields.classList.toggle('hidden', !enrollment);
    }

    tabWelcome.addEventListener('click', () => switchTab('welcome'));
    tabEnrollment.addEventListener('click', () => switchTab('enrollment'));
    switchTab('welcome');
})();
