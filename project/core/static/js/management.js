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
    document.querySelectorAll('.config-input').forEach(input => {
        if (!input.disabled && input.name) {
            const parsedValue = Number.parseFloat(String(input.value).replace(',', '.'));
            if (Number.isNaN(parsedValue)) {
                showToast('Revisa los importes antes de guardar', 'error');
                return;
            }
            data[input.name] = parsedValue;
        }
    });

    try {
        const response = await fetch(window.MANAGEMENT_CONFIG.updateConfigUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify(data)
        });

        const result = await response.json();

        if (result.success) {
            showToast(result.message, 'success');
            cancelEdit();
        } else {
            showToast(result.message, 'error');
        }
    } catch (error) {
        showToast('Error al guardar la configuración', 'error');
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
        const response = await fetch(window.MANAGEMENT_CONFIG.createTeacherUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify(data)
        });

        const result = await response.json();

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
        showToast('Error al crear el profesor', 'error');
    }
});

// Nuevo Grupo
const btnNewGroup = document.getElementById('btn-new-group');
if (btnNewGroup) btnNewGroup.addEventListener('click', async function() {
    // Refrescar lista de profesores antes de abrir
    try {
        const response = await fetch(window.MANAGEMENT_CONFIG.getTeachersUrl);
        const data = await response.json();

        const select = document.getElementById('select-teacher');
        select.innerHTML = '<option value="">Seleccionar profesor...</option>';

        data.teachers.forEach(teacher => {
            const option = document.createElement('option');
            option.value = teacher.id;
            option.textContent = teacher.full_name;
            select.appendChild(option);
        });
    } catch (error) {
        console.error('Error al cargar profesores:', error);
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
        const response = await fetch(window.MANAGEMENT_CONFIG.createGroupUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify(data)
        });

        const result = await response.json();

        if (result.success) {
            showToast(result.message, 'success');
            closeModal('modal-group');
            // Recargar página para ver el nuevo grupo
            setTimeout(() => location.reload(), 1000);
        } else {
            showToast(result.message, 'error');
        }
    } catch (error) {
        showToast('Error al crear el grupo', 'error');
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
        const response = await fetch(window.MANAGEMENT_CONFIG.changePasswordUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify(data)
        });

        // The rate limiter answers 429 as text/plain, so response.json()
        // would throw and the real reason ("too many attempts") would surface
        // as a generic error.
        if (response.status === 429) {
            showToast('Demasiados intentos. Prueba de nuevo en unos minutos.', 'error');
            return;
        }

        const result = await response.json();

        if (result.success) {
            showToast(result.message, 'success');
            closeModal('modal-password');
            this.reset();
        } else {
            showToast(result.message, 'error');
        }
    } catch (error) {
        showToast('Error al cambiar la contraseña', 'error');
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
