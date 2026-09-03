/**
 * students.js — Student list: search, sort, Fun Friday toggle/filter, student
 * type / GDPR / allergy filters, new-student dropdown, "Nueva matrícula" modal.
 * No Django template variables required.
 */
document.addEventListener('DOMContentLoaded', function () {
    // NOTE: this page used to carry an "add/edit student" modal. It was dead:
    // its form had no action (so creating POSTed to /students/, a ListView →
    // 405), `editStudent()` fetched /students/<id>/ and called .json() on an
    // HTML response (so editing always errored), and nothing in the template
    // ever opened it. Creating now goes through the "Nuevo Estudiante"
    // dropdown and editing through the per-row pencil → student_update.

    // ==================== SEARCH & SORT ====================
    const studentSearchBtn = document.getElementById('studentSearchBtn');
    const studentSearchInput = document.getElementById('studentSearchInput');
    const studentSortBtn = document.getElementById('studentSortBtn');
    const studentSortIcon = document.getElementById('studentSortIcon');
    const studentsTableBody = document.getElementById('studentsTableBody');

    // Each row carries independent visibility flags: search, Fun Friday, type,
    // GDPR and allergies. A row is shown only if no flag is set.
    function applyVisibility() {
        studentsTableBody.querySelectorAll('tr[data-name]').forEach(row => {
            row.style.display = (
                row._searchHidden || row._ffHidden || row._typeHidden ||
                row._gdprHidden || row._allergyHidden
            ) ? 'none' : '';
        });
    }

    // Search toggle
    studentSearchBtn.addEventListener('click', () => {
        const visible = studentSearchInput.style.display !== 'none';
        if (visible) {
            studentSearchInput.style.display = 'none';
            studentSearchInput.value = '';
            studentsTableBody.querySelectorAll('tr[data-name]').forEach(r => { r._searchHidden = false; });
            applyVisibility();
        } else {
            studentSearchInput.style.display = 'block';
            studentSearchInput.focus();
        }
    });

    studentSearchInput.addEventListener('input', function() {
        const q = this.value.toLowerCase().trim();
        studentsTableBody.querySelectorAll('tr[data-name]').forEach(row => {
            row._searchHidden = q !== '' && !row.dataset.name.toLowerCase().includes(q);
        });
        applyVisibility();
    });

    // Sort — cycles: date ↑ → date ↓ → name A→Z → name Z→A → repeat
    let studentSortState = 0;
    const studentSortCfg = [
        { field: 'date', dir: 'asc',  icon: 'calendar_month', title: 'Fecha ↑' },
        { field: 'date', dir: 'desc', icon: 'calendar_month', title: 'Fecha ↓' },
        { field: 'name', dir: 'asc',  icon: 'sort_by_alpha',  title: 'Nombre A→Z' },
        { field: 'name', dir: 'desc', icon: 'sort_by_alpha',  title: 'Nombre Z→A' },
    ];

    studentSortBtn.addEventListener('click', () => {
        studentSortState = (studentSortState + 1) % 4;
        const cfg = studentSortCfg[studentSortState];
        studentSortIcon.textContent = cfg.icon;
        studentSortBtn.title = cfg.title;
        const rows = Array.from(studentsTableBody.querySelectorAll('tr[data-name]'));
        rows.sort((a, b) => {
            if (cfg.field === 'name') {
                const na = a.dataset.name.toLowerCase(), nb = b.dataset.name.toLowerCase();
                return cfg.dir === 'asc' ? na.localeCompare(nb) : nb.localeCompare(na);
            } else {
                const da = parseInt(a.dataset.date), db = parseInt(b.dataset.date);
                return cfg.dir === 'asc' ? da - db : db - da;
            }
        });
        rows.forEach(row => studentsTableBody.appendChild(row));
    });

    // ==================== FUN FRIDAY ====================
    function getCsrf() {
        return document.querySelector('[name=csrfmiddlewaretoken]')?.value
            || document.cookie.split(';').map(c=>c.trim()).find(c=>c.startsWith('csrftoken='))?.split('=')[1]
            || '';
    }

    // Returns sort priority 1(green)→2(yellow✓)→3(yellow✗)→4(grey)
    function getFFCategory(row) {
        const t = row.dataset.ffThis === '1', l = row.dataset.ffLast === '1';
        if (t && !l) return 1;
        if (t && l)  return 2;
        if (!t && l) return 3;
        return 4;
    }

    function updateFFIcon(btn, isThis, isLast) {
        const span = btn.querySelector('.ff-icon');
        const row = btn.closest('tr');
        row.dataset.ffThis = isThis ? '1' : '0';
        if (isThis && !isLast)       { span.textContent = 'check_circle'; span.style.color = '#22c55e'; }
        else if (isThis && isLast)   { span.textContent = 'check_circle'; span.style.color = '#f59e0b'; }
        else if (!isThis && isLast)  { span.textContent = 'cancel';       span.style.color = '#f59e0b'; }
        else                         { span.textContent = 'cancel';       span.style.color = '#d1d5db'; }
    }

    document.querySelectorAll('.ff-toggle-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const studentId = this.dataset.studentId;
            fetch(`/api/students/${studentId}/fun-friday/toggle/`, {
                method: 'POST',
                headers: {'Content-Type':'application/json','X-CSRFToken':getCsrf()},
                body: '{}',
            }).then(r=>r.json()).then(data => {
                if (data.success) {
                    updateFFIcon(this, data.is_this_week, data.was_last_week);
                    applyFFFilter();
                }
            });
        });
    });

    // FF Filter: 0=all → 1=not-in-this-week → 2=in-this-week → 0
    const studentFFFilterBtn = document.getElementById('studentFFFilterBtn');
    const studentFFFilterIcon = document.getElementById('studentFFFilterIcon');
    let ffFilterState = 0;

    function applyFFFilter() {
        studentsTableBody.querySelectorAll('tr[data-name]').forEach(row => {
            const isThis = row.dataset.ffThis === '1';
            row._ffHidden = (ffFilterState === 1 && isThis) || (ffFilterState === 2 && !isThis);
        });
        applyVisibility();
    }

    const ffFilterCfg = [
        { icon: 'celebration',   title: 'Filtrar por Fun Friday',          bg: '',        color: '' },
        { icon: 'cancel',        title: 'Mostrando: Sin FF esta semana',   bg: '#6b7280', color: '#ffffff' },
        { icon: 'check_circle',  title: 'Mostrando: Con FF esta semana',   bg: '#22c55e', color: '#ffffff' },
    ];

    studentFFFilterBtn.addEventListener('click', () => {
        ffFilterState = (ffFilterState + 1) % 3;
        const cfg = ffFilterCfg[ffFilterState];
        studentFFFilterIcon.textContent = cfg.icon;
        studentFFFilterBtn.title = cfg.title;
        studentFFFilterBtn.style.background = cfg.bg;
        studentFFFilterBtn.style.color = cfg.color;
        applyFFFilter();
    });

    // ==================== STUDENT TYPE FILTER (All / Children / Adults) ====================
    const studentTypeFilterBtn = document.getElementById('studentTypeFilterBtn');
    const studentTypeFilterIcon = document.getElementById('studentTypeFilterIcon');
    let typeFilterState = 0; // 0=all, 1=children, 2=adults

    const typeFilterCfg = [
        { icon: 'groups',       title: 'Todos los estudiantes',   bg: '', color: '' },
        { icon: 'child_care',   title: 'Solo niños',              bg: '#3b82f6', color: '#ffffff' },
        { icon: 'person',       title: 'Solo adultos',            bg: '#f59e0b', color: '#ffffff' },
        { icon: 'translate',    title: 'Cheque idioma',            bg: '#059669', color: '#ffffff' },
    ];

    function applyTypeFilter() {
        studentsTableBody.querySelectorAll('tr[data-name]').forEach(row => {
            const isAdult = row.dataset.isAdult === '1';
            const hasLC = row.dataset.hasLc === '1';
            if (typeFilterState === 0) row._typeHidden = false;
            else if (typeFilterState === 1) row._typeHidden = isAdult;
            else if (typeFilterState === 2) row._typeHidden = !isAdult;
            else if (typeFilterState === 3) row._typeHidden = !hasLC;
        });
        applyVisibility();
    }

    studentTypeFilterBtn.addEventListener('click', () => {
        typeFilterState = (typeFilterState + 1) % 4;
        const cfg = typeFilterCfg[typeFilterState];
        studentTypeFilterIcon.textContent = cfg.icon;
        studentTypeFilterBtn.title = cfg.title;
        studentTypeFilterBtn.style.background = cfg.bg;
        studentTypeFilterBtn.style.color = cfg.color;
        applyTypeFilter();
    });

    // ==================== GDPR FILTER (All / Signed / Not signed) ====================
    // Used to check whose face may appear in newsletters and photos.
    const studentGdprFilterBtn = document.getElementById('studentGdprFilterBtn');
    const studentGdprFilterIcon = document.getElementById('studentGdprFilterIcon');
    let gdprFilterState = 0;

    const gdprFilterCfg = [
        { icon: 'policy',       title: 'RGPD: Todos',            bg: '',        color: '' },
        { icon: 'verified_user', title: 'RGPD firmado',          bg: '#059669', color: '#ffffff' },
        { icon: 'gpp_maybe',    title: 'RGPD SIN firmar',        bg: '#dc2626', color: '#ffffff' },
    ];

    function applyGdprFilter() {
        studentsTableBody.querySelectorAll('tr[data-name]').forEach(row => {
            const signed = row.dataset.gdpr === '1';
            if (gdprFilterState === 0) row._gdprHidden = false;
            else if (gdprFilterState === 1) row._gdprHidden = !signed;
            else row._gdprHidden = signed;
        });
        applyVisibility();
    }

    if (studentGdprFilterBtn) {
        studentGdprFilterBtn.addEventListener('click', () => {
            gdprFilterState = (gdprFilterState + 1) % gdprFilterCfg.length;
            const cfg = gdprFilterCfg[gdprFilterState];
            studentGdprFilterIcon.textContent = cfg.icon;
            studentGdprFilterBtn.title = cfg.title;
            studentGdprFilterBtn.style.background = cfg.bg;
            studentGdprFilterBtn.style.color = cfg.color;
            applyGdprFilter();
        });
    }

    // ==================== ALLERGY FILTER (All / With allergies) ====================
    const studentAllergyFilterBtn = document.getElementById('studentAllergyFilterBtn');
    const studentAllergyFilterIcon = document.getElementById('studentAllergyFilterIcon');
    let allergyFilterState = 0;

    const allergyFilterCfg = [
        { icon: 'allergies',  title: 'Alergias: Todos',      bg: '',        color: '' },
        { icon: 'warning',    title: 'Solo CON alergias',    bg: '#d97706', color: '#ffffff' },
        { icon: 'check_circle', title: 'Solo SIN alergias',  bg: '#059669', color: '#ffffff' },
    ];

    function applyAllergyFilter() {
        studentsTableBody.querySelectorAll('tr[data-name]').forEach(row => {
            const hasAllergies = row.dataset.allergies === '1';
            if (allergyFilterState === 0) row._allergyHidden = false;
            else if (allergyFilterState === 1) row._allergyHidden = !hasAllergies;
            else row._allergyHidden = hasAllergies;
        });
        applyVisibility();
    }

    if (studentAllergyFilterBtn) {
        studentAllergyFilterBtn.addEventListener('click', () => {
            allergyFilterState = (allergyFilterState + 1) % allergyFilterCfg.length;
            const cfg = allergyFilterCfg[allergyFilterState];
            studentAllergyFilterIcon.textContent = cfg.icon;
            studentAllergyFilterBtn.title = cfg.title;
            studentAllergyFilterBtn.style.background = cfg.bg;
            studentAllergyFilterBtn.style.color = cfg.color;
            applyAllergyFilter();
        });
    }

    // ==================== NUEVA MATRÍCULA (book icon + modal) ====================
    const enrollModal = document.getElementById('enrollModal');
    if (enrollModal) {
        const enrollForm = document.getElementById('enrollForm');
        const enrollNameEl = document.getElementById('enrollStudentName');
        const enrollErrorEl = document.getElementById('enrollError');
        const enrollSpecialCb = document.getElementById('id_is_special');
        const enrollManualRow = document.getElementById('enrollManualRow');
        const enrollSpecialFeeRow = document.getElementById('enrollSpecialFeeRow');
        const enrollSubmitBtn = document.getElementById('enrollSubmitBtn');
        let enrollStudentId = null;

        function closeEnrollModal() { enrollModal.style.display = 'none'; }

        function toggleSpecialRows() {
            const on = enrollSpecialCb.checked;
            enrollManualRow.style.display = on ? '' : 'none';
            enrollSpecialFeeRow.style.display = on ? '' : 'none';
        }

        document.querySelectorAll('.enroll-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                enrollStudentId = btn.dataset.studentId;
                // textContent, never innerHTML — the name is user-entered text.
                enrollNameEl.textContent = btn.dataset.studentName;
                // reset() restores the server-rendered defaults: today's start
                // date, "cobrar matrícula" checked, everything else clear.
                enrollForm.reset();
                // Adults have no plan choice (the service prices adult_group) and
                // no child discounts, mirroring the create page's adult mode.
                const isAdult = btn.dataset.isAdult === '1';
                enrollModal.querySelectorAll('.enroll-child-only').forEach(el => {
                    el.style.display = isAdult ? 'none' : '';
                    const box = el.querySelector('input[type="checkbox"]');
                    if (isAdult && box) box.checked = false;
                });
                toggleSpecialRows();
                enrollErrorEl.style.display = 'none';
                enrollModal.style.display = 'flex';
            });
        });

        enrollSpecialCb.addEventListener('change', toggleSpecialRows);
        document.getElementById('enrollModalClose').addEventListener('click', closeEnrollModal);
        document.getElementById('enrollCancelBtn').addEventListener('click', closeEnrollModal);
        enrollModal.addEventListener('click', (e) => { if (e.target === enrollModal) closeEnrollModal(); });

        enrollForm.addEventListener('submit', function (e) {
            e.preventDefault();
            if (!enrollStudentId) return;
            enrollSubmitBtn.disabled = true;
            fetch(`/api/students/${enrollStudentId}/enroll/`, {
                method: 'POST',
                headers: { 'X-CSRFToken': getCsrf() },
                body: new FormData(enrollForm),
            })
                .then(r => r.json().then(data => ({ ok: r.ok, data })))
                .then(({ ok, data }) => {
                    if (ok && data.success) {
                        // The Matrícula column and payments changed server-side.
                        window.location.reload();
                        return;
                    }
                    enrollErrorEl.textContent = data.error || 'Error al crear la matrícula.';
                    enrollErrorEl.style.display = '';
                    enrollSubmitBtn.disabled = false;
                })
                .catch(() => {
                    enrollErrorEl.textContent = 'Error de conexión. Inténtalo de nuevo.';
                    enrollErrorEl.style.display = '';
                    enrollSubmitBtn.disabled = false;
                });
        });
    }

    // ==================== NEW STUDENT DROPDOWN ====================
    // The whole "Nuevo Estudiante" dropdown is admin-only, so it is absent from
    // the DOM for a non-admin teacher — guard, or the TypeError takes down the
    // rest of this handler with it.
    const newStudentDropdown = document.getElementById('newStudentDropdown');
    const newStudentBtn = document.getElementById('newStudentBtn');
    const newStudentMenu = document.getElementById('newStudentMenu');
    const newStudentArrow = document.getElementById('newStudentArrow');

    if (newStudentDropdown && newStudentBtn && newStudentMenu && newStudentArrow) {
        newStudentBtn.addEventListener('click', () => {
            const open = !newStudentMenu.classList.contains('hidden');
            newStudentMenu.classList.toggle('hidden');
            newStudentArrow.textContent = open ? 'expand_more' : 'expand_less';
        });

        document.addEventListener('click', (e) => {
            if (!newStudentDropdown.contains(e.target)) {
                newStudentMenu.classList.add('hidden');
                newStudentArrow.textContent = 'expand_more';
            }
        });
    }
});
