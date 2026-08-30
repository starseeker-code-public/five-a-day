// payments.js — merged from payments_list.html and payment_create.html

// ============================================================
// PAYMENTS LIST (payments_list.html)
// ============================================================
(function initPaymentsList() {
    const paymentsTableBody = document.getElementById('paymentsTableBody');
    if (!paymentsTableBody) return;

    const PAGE_SIZE = 10;
    let currentPage = 1;

    function getCsrf() {
        return document.querySelector('[name=csrfmiddlewaretoken]')?.value
            || document.cookie.split(';').map(c=>c.trim()).find(c=>c.startsWith('csrftoken='))?.split('=')[1]
            || '';
    }

    // Get all data rows (cached once)
    const allRows = Array.from(paymentsTableBody.querySelectorAll('tr[data-name]'));

    // ==================== VISIBILITY + PAGINATION ====================
    function getVisibleRows() {
        return allRows.filter(row => !row._searchHidden && !row._typeHidden && !row._statusHidden);
    }

    function renderPage() {
        const visible = getVisibleRows();
        const totalPages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
        if (currentPage > totalPages) currentPage = totalPages;
        const start = (currentPage - 1) * PAGE_SIZE;
        const end = start + PAGE_SIZE;

        // Hide all rows first
        allRows.forEach(row => row.style.display = 'none');
        // Show only current page of visible rows
        visible.forEach((row, i) => {
            row.style.display = (i >= start && i < end) ? '' : 'none';
        });

        // Update count
        document.getElementById('visibleCount').textContent = visible.length;

        // Render pagination controls
        renderPagination(totalPages);
    }

    function renderPagination(totalPages) {
        const nav = document.getElementById('paginationNav');
        if (totalPages <= 1) { nav.innerHTML = ''; return; }

        let html = '';
        if (currentPage > 1) {
            html += `<button type="button" class="pg-btn" data-page="${currentPage - 1}" style="font-size:1rem;">\u2039</button>`;
        }

        // Show limited page range for many pages
        let pages = [];
        if (totalPages <= 7) {
            for (let i = 1; i <= totalPages; i++) pages.push(i);
        } else {
            pages = [1];
            let start = Math.max(2, currentPage - 1);
            let end = Math.min(totalPages - 1, currentPage + 1);
            if (start > 2) pages.push('...');
            for (let i = start; i <= end; i++) pages.push(i);
            if (end < totalPages - 1) pages.push('...');
            pages.push(totalPages);
        }

        for (const p of pages) {
            if (p === '...') {
                html += `<span class="pg-ellipsis">\u2026</span>`;
            } else if (p === currentPage) {
                html += `<span class="pg-active">${p}</span>`;
            } else {
                html += `<button type="button" class="pg-btn" data-page="${p}">${p}</button>`;
            }
        }

        if (currentPage < totalPages) {
            html += `<button type="button" class="pg-btn" data-page="${currentPage + 1}" style="font-size:1rem;">\u203A</button>`;
        }
        nav.innerHTML = html;

        // Attach click handlers
        nav.querySelectorAll('.pg-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                currentPage = parseInt(btn.dataset.page);
                renderPage();
                window.scrollTo({ top: 0, behavior: 'smooth' });
            });
        });
    }

    function applyFiltersAndPaginate() {
        currentPage = 1;
        renderPage();
    }

    // ==================== SEARCH ====================
    const paymentSearchBtn = document.getElementById('paymentSearchBtn');
    const paymentSearchInput = document.getElementById('paymentSearchInput');

    paymentSearchBtn.addEventListener('click', () => {
        const visible = paymentSearchInput.style.display !== 'none';
        if (visible) {
            paymentSearchInput.style.display = 'none';
            paymentSearchInput.value = '';
            allRows.forEach(r => { r._searchHidden = false; });
            applyFiltersAndPaginate();
        } else {
            paymentSearchInput.style.display = 'block';
            paymentSearchInput.focus();
        }
    });

    paymentSearchInput.addEventListener('input', function() {
        const q = this.value.toLowerCase().trim();
        allRows.forEach(row => {
            row._searchHidden = q !== '' && !row.dataset.name.toLowerCase().includes(q);
        });
        applyFiltersAndPaginate();
    });

    // ==================== TYPE FILTER (Monthly 2d/w, Monthly 1d/w, Quarterly) ====================
    const paymentTypeFilterBtn = document.getElementById('paymentTypeFilterBtn');
    const paymentTypeFilterIcon = document.getElementById('paymentTypeFilterIcon');
    let typeFilterState = 0;

    const typeFilterCfg = [
        { icon: 'tune',           title: 'Tipo: Todos',                 bg: '',        color: '', match: null },
        { icon: 'event_repeat',   title: 'Mensual 2 d\u00edas/sem',          bg: '#3b82f6', color: '#fff', match: r => r.dataset.paymentType === 'monthly' && r.dataset.scheduleType === 'full_time' },
        { icon: 'event_note',     title: 'Mensual 1 d\u00eda/sem',           bg: '#8b5cf6', color: '#fff', match: r => r.dataset.paymentType === 'monthly' && (r.dataset.scheduleType === 'part_time' || r.dataset.scheduleType === 'adult_group') },
        { icon: 'date_range',     title: 'Trimestral',                  bg: '#059669', color: '#fff', match: r => r.dataset.paymentType === 'quarterly' },
    ];

    function applyTypeFilter() {
        const cfg = typeFilterCfg[typeFilterState];
        allRows.forEach(row => {
            row._typeHidden = cfg.match ? !cfg.match(row) : false;
        });
        applyFiltersAndPaginate();
    }

    paymentTypeFilterBtn.addEventListener('click', () => {
        typeFilterState = (typeFilterState + 1) % typeFilterCfg.length;
        const cfg = typeFilterCfg[typeFilterState];
        paymentTypeFilterIcon.textContent = cfg.icon;
        paymentTypeFilterBtn.title = cfg.title;
        paymentTypeFilterBtn.style.background = cfg.bg;
        paymentTypeFilterBtn.style.color = cfg.color;
        applyTypeFilter();
    });

    // ==================== STATUS FILTER (All / Not completed) ====================
    const paymentStatusFilterBtn = document.getElementById('paymentStatusFilterBtn');
    const paymentStatusFilterIcon = document.getElementById('paymentStatusFilterIcon');
    let statusFilterState = 0;

    const statusFilterCfg = [
        { icon: 'filter_list',     title: 'Estado: Todos',        bg: '',        color: '' },
        { icon: 'pending_actions', title: 'No completados',       bg: '#dc2626', color: '#fff' },
    ];

    function applyStatusFilter() {
        allRows.forEach(row => {
            if (statusFilterState === 0) {
                row._statusHidden = false;
            } else {
                row._statusHidden = row.dataset.paymentStatus === 'completed';
            }
        });
        applyFiltersAndPaginate();
    }

    paymentStatusFilterBtn.addEventListener('click', () => {
        statusFilterState = (statusFilterState + 1) % statusFilterCfg.length;
        const cfg = statusFilterCfg[statusFilterState];
        paymentStatusFilterIcon.textContent = cfg.icon;
        paymentStatusFilterBtn.title = cfg.title;
        paymentStatusFilterBtn.style.background = cfg.bg;
        paymentStatusFilterBtn.style.color = cfg.color;
        applyStatusFilter();
    });

    // ==================== SORT ====================
    const paymentSortBtn = document.getElementById('paymentSortBtn');
    const paymentSortIcon = document.getElementById('paymentSortIcon');
    let paymentSortState = 0;
    const paymentSortCfg = [
        { field: 'date', dir: 'asc',  icon: 'calendar_month', title: 'Fecha \u2191' },
        { field: 'date', dir: 'desc', icon: 'calendar_month', title: 'Fecha \u2193' },
        { field: 'name', dir: 'asc',  icon: 'sort_by_alpha',  title: 'Nombre A\u2192Z' },
        { field: 'name', dir: 'desc', icon: 'sort_by_alpha',  title: 'Nombre Z\u2192A' },
    ];

    paymentSortBtn.addEventListener('click', () => {
        paymentSortState = (paymentSortState + 1) % 4;
        const cfg = paymentSortCfg[paymentSortState];
        paymentSortIcon.textContent = cfg.icon;
        paymentSortBtn.title = cfg.title;
        // Sort the cached allRows array (this reorders the canonical list)
        allRows.sort((a, b) => {
            if (cfg.field === 'name') {
                const na = a.dataset.name.toLowerCase(), nb = b.dataset.name.toLowerCase();
                return cfg.dir === 'asc' ? na.localeCompare(nb) : nb.localeCompare(na);
            }
            return cfg.dir === 'asc'
                ? a.dataset.date.localeCompare(b.dataset.date)
                : b.dataset.date.localeCompare(a.dataset.date);
        });
        // Re-append in new order
        allRows.forEach(row => paymentsTableBody.appendChild(row));
        renderPage();
    });

    // ==================== PAYMENT COMPLETION DROPDOWN ====================
    document.querySelectorAll('.payment-complete-trigger').forEach(trigger => {
        trigger.addEventListener('click', function(e) {
            e.stopPropagation();
            document.querySelectorAll('.payment-dropdown').forEach(d => {
                if (d !== this.querySelector('.payment-dropdown')) d.classList.add('hidden');
            });
            this.querySelector('.payment-dropdown').classList.toggle('hidden');
        });
    });

    document.addEventListener('click', () => {
        document.querySelectorAll('.payment-dropdown').forEach(d => d.classList.add('hidden'));
    });

    document.querySelectorAll('.payment-method-btn').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const paymentId = this.dataset.paymentId;
            const method = this.dataset.method;

            fetch(`/api/payments/${paymentId}/quick-complete/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrf(),
                },
                body: JSON.stringify({ payment_method: method }),
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    const row = document.querySelector(`tr[data-payment-id="${paymentId}"]`);
                    if (row) {
                        row.dataset.paymentStatus = 'completed';
                        const trigger = row.querySelector('.payment-complete-trigger');
                        if (trigger) trigger.remove();
                        const statusCell = row.querySelectorAll('td')[5];
                        if (statusCell) {
                            statusCell.innerHTML = '<span class="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-green-100 text-green-800"><span class="material-symbols-outlined text-sm mr-1">check_circle</span>Completado</span>';
                        }
                        const payDateCell = row.querySelectorAll('td')[7];
                        if (payDateCell) {
                            const today = new Date();
                            payDateCell.textContent = `${String(today.getDate()).padStart(2,'0')}/${String(today.getMonth()+1).padStart(2,'0')}/${today.getFullYear()}`;
                        }
                        const methodLabels = { cash: 'Cash', transfer: 'Bank Transfer', credit_card: 'Credit Card' };
                        const methodCell = row.querySelectorAll('td')[4];
                        if (methodCell) {
                            methodCell.innerHTML = `<span class="text-sm text-neutral-800">${methodLabels[method] || method}</span>`;
                        }
                        applyStatusFilter();
                    }
                } else {
                    alert(data.error || 'Error al completar el pago');
                }
            })
            .catch(err => {
                console.error('Error completing payment:', err);
                alert('Error de conexi\u00f3n');
            });

            document.querySelectorAll('.payment-dropdown').forEach(d => d.classList.add('hidden'));
        });
    });

    // ==================== CANCEL PAYMENT ====================
    // Soft-delete: sets payment_status='cancelled' so the row stays for the
    // audit trail but stops counting toward "esperado". Used for duplicates
    // and for students who drop out before a due date.
    document.querySelectorAll('.payment-cancel-btn').forEach(btn => {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            const paymentId = this.dataset.paymentId;
            const student = this.dataset.studentName || 'este alumno';
            const concept = this.dataset.concept ? ` (${this.dataset.concept})` : '';
            if (!confirm(`¿Cancelar el pago de ${student}${concept}?\n\nDejará de contar como pago esperado. Podrás verlo en el historial marcado como "Cancelado".`)) {
                return;
            }
            fetch(`/payments/${paymentId}/deactivate/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
                body: '{}',
            })
            .then(r => r.json())
            .then(data => {
                if (!data.success) {
                    alert(data.message || data.error || 'No se pudo cancelar el pago');
                    return;
                }
                const row = document.querySelector(`tr[data-payment-id="${paymentId}"]`);
                if (row) {
                    row.dataset.paymentStatus = 'cancelled';
                    const statusCell = row.querySelectorAll('td')[5];
                    if (statusCell) {
                        statusCell.innerHTML = '<span class="status-badge inline-flex items-center px-2 py-1 rounded-full text-xs font-bold bg-neutral-100 text-neutral-800"><span class="material-symbols-outlined text-sm mr-2">block</span>Cancelado</span>';
                    }
                    const trigger = row.querySelector('.payment-complete-trigger');
                    if (trigger) trigger.remove();
                    this.remove();
                }
            })
            .catch(() => alert('Error de conexión'));
        });
    });

    // ==================== INIT ====================
    // Initialize filter flags
    allRows.forEach(r => { r._searchHidden = false; r._typeHidden = false; r._statusHidden = false; });
    renderPage();
})();


// ============================================================
// PAYMENT CREATE (payment_create.html)
// ============================================================
(function initPaymentCreate() {
    const studentSearch = document.getElementById('student_search');
    if (!studentSearch) return;

    const studentSuggestions = document.getElementById('student_suggestions');
    const parentDisplay = document.getElementById('parent_display');
    const validationMessage = document.getElementById('validation_message');
    const form = document.getElementById('paymentForm');

    let selectedStudent = null;
    let selectedParent = null;

    // Set today as default due date
    document.getElementById('due_date').value = new Date().toISOString().split('T')[0];

    // Auto-fill payment date when status changes to completed
    document.getElementById('payment_status').addEventListener('change', function() {
        const paymentDate = document.getElementById('payment_date');
        if (this.value === 'completed' && !paymentDate.value) {
            paymentDate.value = new Date().toISOString().split('T')[0];
        }
    });

    // Auto-generate concept based on payment type
    document.getElementById('payment_type').addEventListener('change', function() {
        const concept = document.getElementById('concept');
        if (!concept.value || concept.value.startsWith('Pago de') || concept.value === 'Otro pago') {
            // Keys must match billing.constants.PAYMENT_TYPE_CHOICES.
            const map = {
                enrollment: 'Pago de matr\u00edcula',
                monthly: 'Pago mensualidad',
                quarterly: 'Pago trimestral',
                other: 'Otro pago',
            };
            concept.value = map[this.value] || '';
        }
    });

    // Student search
    let studentTimeout;
    studentSearch.addEventListener('input', function() {
        clearTimeout(studentTimeout);
        const query = this.value.trim();
        if (query.length < 2) { studentSuggestions.classList.add('hidden'); return; }
        studentTimeout = setTimeout(() => {
            fetch(`/api/search/students/?q=${encodeURIComponent(query)}`)
                .then(r => r.json())
                .then(data => displayStudentSuggestions(data.results))
                .catch(e => console.error(e));
        }, 300);
    });

    // Parent is auto-populated from student selection

    function displayStudentSuggestions(students) {
        if (!students.length) { studentSuggestions.classList.add('hidden'); return; }
        // Built as DOM nodes with textContent, NOT innerHTML + inline onclick.
        // Student names are free text; string-interpolating them into an HTML
        // attribute let a name like `Ana" onmouseover="...` execute script.
        studentSuggestions.replaceChildren();
        students.forEach(s => {
            const row = document.createElement('div');
            row.className = 'p-3 hover:bg-neutral-50 cursor-pointer border-b border-neutral-100';

            const name = document.createElement('div');
            name.className = 'font-medium text-neutral-800';
            name.textContent = s.full_name;
            row.appendChild(name);

            if (s.school) {
                const school = document.createElement('div');
                school.className = 'text-sm text-neutral-500';
                school.textContent = s.school;
                row.appendChild(school);
            }

            row.addEventListener('click', () => selectStudent(s));
            studentSuggestions.appendChild(row);
        });
        studentSuggestions.classList.remove('hidden');
    }



    // The parent arrives with the search result, so it is filled in synchronously.
    // This used to be a second POST to /api/validate/student-parent/ whose errors
    // were swallowed by a bare .catch(() => {}) — any failure on that hop left the
    // Padre/Tutor box silently blank with no clue as to why.
    function selectStudent(student) {
        selectedStudent = { id: student.id, name: student.full_name };
        studentSearch.value = student.full_name;
        document.getElementById('student_id').value = student.id;
        studentSuggestions.classList.add('hidden');

        if (student.parent_id) {
            selectedParent = { id: student.parent_id, name: student.parent_name };
            selectedStudent.noParent = false;
            document.getElementById('parent_id').value = student.parent_id;
            if (parentDisplay) parentDisplay.value = student.parent_name;
        } else {
            // Adult students have no parent/guardian — this is valid.
            selectedParent = null;
            selectedStudent.noParent = true;
            document.getElementById('parent_id').value = '';
            if (parentDisplay) parentDisplay.value = 'Sin padre/tutor (estudiante adulto)';
        }
    }

    // `selectParent()` and `validateRelation()` lived here and were never called
    // by anything: the parent field is read-only and filled from the student, so
    // there is no parent picker to validate against. Removed rather than left as
    // unreachable code claiming a contract the form no longer has. The enrollment
    // amount/concept prefill validateRelation() carried was dead with it — ask for
    // it back as a feature if it is wanted.

    // Hide suggestions when clicking outside
    document.addEventListener('click', function(e) {
        if (!studentSearch.contains(e.target) && !studentSuggestions.contains(e.target))
            studentSuggestions.classList.add('hidden');
    });

    // Form validation
    form.addEventListener('submit', (e) => {
        // A student is always required; a parent is required EXCEPT for adult
        // students, who have no parent/guardian (selectedStudent.noParent).
        if (!selectedStudent || (!selectedParent && !selectedStudent.noParent)) {
            e.preventDefault();
            validationMessage.classList.remove('hidden');
            validationMessage.style.color = '#dc2626';
            validationMessage.textContent = '\u26A0 Debe seleccionar un estudiante y un padre/tutor v\u00e1lidos.';
            return;
        }
        // Auto-set payment date if status is completed and date is empty
        const paymentStatus = document.getElementById('payment_status').value;
        const paymentDate = document.getElementById('payment_date');
        if (paymentStatus === 'completed' && !paymentDate.value) {
            paymentDate.value = new Date().toISOString().split('T')[0];
        }
    });
})();
