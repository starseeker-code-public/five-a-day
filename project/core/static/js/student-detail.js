// student-detail.js — extracted from student_detail.html
// Expects window.STUDENT_ID to be set by the template

const STUDENT_ID = window.STUDENT_ID;

// CSRF token + status-checked fetch come from base.js (window.CSRF_TOKEN /
// window.apiFetch). This file used to carry its own copy of the token reader.

function addFunFriday() {
    const inp = document.getElementById('ff-new-date');
    const d = inp.value;
    if (!d) return;
    window.apiFetch(`/api/students/${STUDENT_ID}/fun-friday/add/`, {
        method: 'POST',
        body: JSON.stringify({date: d}),
    }).then(data => {
        if (data.success) { inp.value=''; location.reload(); }
        else alert(data.error);
    }).catch(err => alert(window.apiErrorMessage(err)));
}

function removeFunFriday(d) {
    if (!confirm('\u00bfEliminar esta fecha?')) return;
    window.apiFetch(`/api/students/${STUDENT_ID}/fun-friday/remove/`, {
        method: 'POST',
        body: JSON.stringify({date: d}),
    }).then(data => {
        if (data.success) location.reload();
        else alert(data.error);
    }).catch(err => alert(window.apiErrorMessage(err)));
}

// ==================== MODALITY TOGGLE ====================
document.querySelectorAll('.modality-toggle-btn').forEach(btn => {
    btn.addEventListener('click', function() {
        const studentId = this.dataset.studentId;
        const current = this.dataset.current;
        const newModality = current === 'monthly' ? 'quarterly' : 'monthly';

        if (!confirm(`\u00bfCambiar modalidad de pago a ${newModality === 'monthly' ? 'Mensual' : 'Trimestral'}?`)) return;

        window.apiFetch(`/api/students/${studentId}/enrollment/modality/`, {
            method: 'POST',
            body: JSON.stringify({payment_modality: newModality}),
        }).then(data => {
            if (data.success) {
                // The endpoint SUPERSEDES the enrollment (the current row is
                // finished, a new one is issued from the effective date) and
                // re-schedules the payments, so the Matrículas and Pagos tables
                // both changed server-side. Patching the OLD row's label showed
                // the new cadence on a row that is now "Finalizada" and hid the
                // new one until a manual refresh. Report the effective date —
                // not always today — then reload, like the enroll modal does.
                if (data.message) alert(data.message);
                location.reload();
            } else {
                alert(data.error || 'Error al cambiar modalidad');
            }
        }).catch(err => alert(window.apiErrorMessage(err)));
    });
});

// Fun Friday buttons carry data attributes instead of inline onclick (CSP).
document.addEventListener('click', function (e) {
    const rm = e.target.closest ? e.target.closest('[data-ff-remove]') : null;
    if (rm) { removeFunFriday(rm.getAttribute('data-ff-remove')); return; }
    if (e.target.closest && e.target.closest('[data-ff-add]')) addFunFriday();
});
