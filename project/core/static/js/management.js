// The page still passes its own token through MANAGEMENT_CONFIG (rendered from
// {% csrf_token %}, so it is input-derived and correct); window.apiFetch adds
// window.CSRF_TOKEN when a caller does not supply one. What every request here
// lacked was a STATUS check: `await response.json()` on a 403 (expired session)
// or a 302-to-login threw a SyntaxError that landed in the generic
// `catch (error)` and showed "Error al guardar la configuración" — the user
// re-typed the prices and it failed again.
const csrfToken = window.MANAGEMENT_CONFIG.csrfToken;
let editMode = false;

// Toggle sección desplegable
function toggleSection(sectionId) {
    const section = document.getElementById(sectionId);
    const iconId = sectionId.replace('-section', '-icon');
    const icon = document.getElementById(iconId);

    section.classList.toggle('hidden');
    icon.style.transform = section.classList.contains('hidden') ? '' : 'rotate(180deg)';
}

// Mostrar/ocultar modal
// Accordion headers use data-toggle-section instead of inline onclick (CSP).
document.addEventListener('click', function (e) {
    const hdr = e.target.closest ? e.target.closest('[data-toggle-section]') : null;
    if (hdr) toggleSection(hdr.getAttribute('data-toggle-section'));
});

function openModal(modalId) {
    document.getElementById(modalId).style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

function closeModal(modalId) {
    document.getElementById(modalId).style.display = 'none';
    document.body.style.overflow = '';
}

// Toast de notificación
function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    const toastMessage = document.getElementById('toast-message');
    const toastIcon = document.getElementById('toast-icon');

    toastMessage.textContent = message;

    if (type === 'success') {
        toast.querySelector('div').className = 'bg-green-500 text-white px-6 py-3 rounded-lg flex items-center gap-3';
        toastIcon.textContent = 'check_circle';
    } else {
        toast.querySelector('div').className = 'bg-red-500 text-white px-6 py-3 rounded-lg flex items-center gap-3';
        toastIcon.textContent = 'error';
    }

    toast.classList.remove('translate-y-full', 'opacity-0');

    setTimeout(() => {
        toast.classList.add('translate-y-full', 'opacity-0');
    }, 3000);
}

// The academy's fiscal details (`academy_name`, `academy_cif`, …) are TEXT, so
// they carry `.config-text-input` and are collected as strings. They must never
// wear `.config-input`: that class is run through Number.parseFloat below and a
// NaN aborts the entire save, so one text field there would make every price
// unsaveable.
function setTextInputsEnabled(enabled) {
    document.querySelectorAll('.config-text-input').forEach(input => {
        input.disabled = !enabled;
        input.classList.toggle('bg-neutral-100', !enabled);
        input.classList.toggle('cursor-not-allowed', !enabled);
        input.classList.toggle('bg-white', enabled);
        input.classList.toggle('focus:ring-2', enabled);
        input.classList.toggle('focus:ring-primary-500', enabled);
        input.classList.toggle('focus:border-primary-500', enabled);
    });
}

// Modo edición de configuración
// Every binding below is null-guarded: a non-admin teacher gets this page in
// view-only mode, so the admin controls are absent from the DOM and an
// unguarded addEventListener() threw and aborted the whole script.
const btnEditValues = document.getElementById('btn-edit-values');
if (btnEditValues) btnEditValues.addEventListener('click', function() {
    editMode = !editMode;
    const inputs = document.querySelectorAll('.config-input');
    const saveContainer = document.getElementById('save-config-container');
    const editBtn = document.getElementById('btn-edit-values');

    if (editMode) {
        inputs.forEach(input => {
            input.disabled = false;
            input.classList.remove('bg-neutral-100', 'cursor-not-allowed');
            input.classList.add('bg-white', 'focus:ring-2', 'focus:ring-primary-500', 'focus:border-primary-500');
        });
        setTextInputsEnabled(true);
        saveContainer.classList.remove('hidden');
        editBtn.innerHTML = '<span class="material-symbols-outlined">close</span> Cancelar Edición';
        editBtn.classList.remove('bg-primary-500', 'hover:bg-primary-600');
        editBtn.classList.add('bg-red-500', 'hover:bg-red-600');

        // Abrir sección de precios si está cerrada
        const preciosSection = document.getElementById('precios-section');
        if (preciosSection.classList.contains('hidden')) {
            toggleSection('precios-section');
        }
    } else {
        cancelEdit();
    }
});

function cancelEdit() {
    editMode = false;
    const inputs = document.querySelectorAll('.config-input');
    const saveContainer = document.getElementById('save-config-container');
    const editBtn = document.getElementById('btn-edit-values');

    inputs.forEach(input => {
        input.disabled = true;
        input.classList.add('bg-neutral-100', 'cursor-not-allowed');
        input.classList.remove('bg-white', 'focus:ring-2', 'focus:ring-primary-500', 'focus:border-primary-500');
    });
    setTextInputsEnabled(false);
    saveContainer.classList.add('hidden');
    editBtn.innerHTML = '<span class="material-symbols-outlined">edit</span> Cambiar Valores';
    editBtn.classList.add('bg-primary-500', 'hover:bg-primary-600');
    editBtn.classList.remove('bg-red-500', 'hover:bg-red-600');
}

const btnCancelEdit = document.getElementById('btn-cancel-edit');
if (btnCancelEdit) btnCancelEdit.addEventListener('click', cancelEdit);

// Guardar configuración
const btnSaveConfig = document.getElementById('btn-save-config');
if (btnSaveConfig) btnSaveConfig.addEventListener('click', async function() {
    const data = {};
    let invalid = null;
    document.querySelectorAll('.config-input').forEach(input => {
        if (!input.disabled && input.name) {
            const parsedValue = Number.parseFloat(String(input.value).replace(',', '.'));
            if (Number.isNaN(parsedValue)) {
                // `return` here only skipped ONE input — the loop finished, the
                // bad field was dropped from `data`, the request fired anyway
                // and update_site_config saved the rest, so a mistyped fee
                // showed a green "guardado" while the OLD price stayed live.
                // Record it and ABORT the whole save below.
                if (!invalid) invalid = input;
                return;
            }
            data[input.name] = parsedValue;
        }
    });

    if (invalid) {
        showToast('Revisa los importes: hay un valor no válido. No se ha guardado nada.', 'error');
        if (invalid.focus) invalid.focus();
        return;
    }

    // Academy fiscal details — strings, sent verbatim (all five are blank=True,
    // so an empty box legitimately clears the field).
    document.querySelectorAll('.config-text-input').forEach(input => {
        if (!input.disabled && input.name) {
            data[input.name] = input.value.trim();
        }
    });

    try {
        const result = await window.apiFetch(window.MANAGEMENT_CONFIG.updateConfigUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken },
            body: JSON.stringify(data)
        });

        if (result.success) {
            showToast(result.message, 'success');
            cancelEdit();
        } else {
            showToast(result.message, 'error');
        }
    } catch (error) {
        showToast(window.apiErrorMessage(error), 'error');
    }
});

// Nuevo Profesor
const btnNewTeacher = document.getElementById('btn-new-teacher');
if (btnNewTeacher) btnNewTeacher.addEventListener('click', function() {
    openModal('modal-teacher');
});

const formTeacher = document.getElementById('form-teacher');
if (formTeacher) formTeacher.addEventListener('submit', async function(e) {
    e.preventDefault();

    const formData = new FormData(this);
    const data = {
        first_name: formData.get('first_name'),
        last_name: formData.get('last_name'),
        email: formData.get('email'),
        phone: formData.get('phone') || '',
        admin: formData.get('admin') === 'on'
    };

    try {
        const result = await window.apiFetch(window.MANAGEMENT_CONFIG.createTeacherUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken },
            body: JSON.stringify(data)
        });

        if (result.success) {
            showToast(result.message, 'success');
            closeModal('modal-teacher');
            // Recargar página para ver el nuevo profesor. 2.5 s, not 1 s: the
            // toast now says whether the activation email went out, and a
            // faster reload wiped that sentence before it could be read.
            setTimeout(() => location.reload(), 2500);
        } else {
            showToast(result.message, 'error');
        }
    } catch (error) {
        showToast(window.apiErrorMessage(error), 'error');
    }
});

// Nuevo Grupo
const btnNewGroup = document.getElementById('btn-new-group');
if (btnNewGroup) btnNewGroup.addEventListener('click', async function() {
    // Refrescar lista de profesores antes de abrir
    try {
        const data = await window.apiFetch(window.MANAGEMENT_CONFIG.getTeachersUrl);

        const select = document.getElementById('select-teacher');
        select.replaceChildren();
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = 'Seleccionar profesor...';
        select.appendChild(blank);

        data.teachers.forEach(teacher => {
            const option = document.createElement('option');
            option.value = teacher.id;
            option.textContent = teacher.full_name;
            select.appendChild(option);
        });
    } catch (error) {
        // Silent console.error left the modal open with an EMPTY teacher list
        // and no explanation, and the group form requires a teacher.
        showToast(window.apiErrorMessage(error), 'error');
    }

    openModal('modal-group');
});

const formGroup = document.getElementById('form-group');
if (formGroup) formGroup.addEventListener('submit', async function(e) {
    e.preventDefault();

    const formData = new FormData(this);
    const data = {
        group_name: formData.get('group_name'),
        color: formData.get('color') || '#6366f1',
        teacher_id: parseInt(formData.get('teacher_id')),
        // Cupo máximo: only set at creation. Empty falls back to the server default (8).
        max_students: formData.get('max_students')
    };

    try {
        const result = await window.apiFetch(window.MANAGEMENT_CONFIG.createGroupUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken },
            body: JSON.stringify(data)
        });

        if (result.success) {
            showToast(result.message, 'success');
            closeModal('modal-group');
            // Recargar página para ver el nuevo grupo
            setTimeout(() => location.reload(), 1000);
        } else {
            showToast(result.message, 'error');
        }
    } catch (error) {
        showToast(window.apiErrorMessage(error), 'error');
    }
});

// Cambiar Contraseña (cuenta propia)
// Absent for Google-OAuth sessions and for accounts with no usable password —
// the server refuses those too, this is not the only gate.
const btnChangePassword = document.getElementById('btn-change-password');
if (btnChangePassword) btnChangePassword.addEventListener('click', function() {
    const form = document.getElementById('form-password');
    if (form) form.reset();
    openModal('modal-password');
});

const formPassword = document.getElementById('form-password');
if (formPassword) formPassword.addEventListener('submit', async function(e) {
    e.preventDefault();

    const formData = new FormData(this);
    const data = {
        current_password: formData.get('current_password'),
        new_password: formData.get('new_password'),
        confirm_password: formData.get('confirm_password')
    };

    if (data.new_password !== data.confirm_password) {
        showToast('Las contraseñas nuevas no coinciden', 'error');
        return;
    }

    try {
        // apiFetch turns the rate limiter's 429 (answered as text/plain, so
        // .json() would throw) into a rejection carrying the "demasiados
        // intentos" message, and a 403 into "sesión caducada" — the two
        // outcomes this endpoint actually produces besides success.
        const result = await window.apiFetch(window.MANAGEMENT_CONFIG.changePasswordUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken },
            body: JSON.stringify(data)
        });

        if (result.success) {
            showToast(result.message, 'success');
            closeModal('modal-password');
            this.reset();
        } else {
            showToast(result.message, 'error');
        }
    } catch (error) {
        showToast(window.apiErrorMessage(error), 'error');
    }
});

// Cerrar modales al hacer click fuera
document.querySelectorAll('[id^="modal-"]').forEach(modal => {
    modal.addEventListener('click', function(e) {
        if (e.target === this) {
            closeModal(this.id);
        }
    });
});

// Cerrar modales con Escape
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        document.querySelectorAll('[id^="modal-"]').forEach(modal => {
            if (modal.style.display !== 'none') closeModal(modal.id);
        });
    }
});
