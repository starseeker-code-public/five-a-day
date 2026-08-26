// schedule.js — extracted from schedule.html
// Expects window.SCHEDULE_CONFIG = { groups, students, slots }

// ── Global edit-mode flag (must be global for onclick= to work) ──
var scheduleEditMode = false;
function toggleEditMode() {
    const btn = document.getElementById('edit-toggle-btn');
    if (!btn) return;  // non-admin teachers: read-only schedule, no edit control
    scheduleEditMode = !scheduleEditMode;
    btn.style.background = scheduleEditMode ? '#e0f2fe' : 'var(--sched-surface)';
    btn.querySelector('.material-symbols-outlined').style.color = scheduleEditMode ? '#0284c7' : 'var(--sched-ink)';
    btn.title = scheduleEditMode ? 'Salir de edición' : 'Editar horario';
    if (window._scheduleRenderTable) window._scheduleRenderTable();
}
window.toggleEditMode = toggleEditMode;

document.addEventListener('DOMContentLoaded', function () {
    const groups = window.SCHEDULE_CONFIG.groups;
    const allStudents = window.SCHEDULE_CONFIG.students;
    const slotsRaw = window.SCHEDULE_CONFIG.slots;

    const FF_CLR = { bg: 'var(--sched-ff-bg)', text: 'var(--sched-ff-text)', dot: '#a78bfa' };

    const groupById = {};
    groups.forEach(g => { groupById[g.id] = g; });

    // ── Color helpers ───────────────────────────────────────────
    function hexToRgb(hex) {
        const r = parseInt(hex.slice(1,3),16);
        const g = parseInt(hex.slice(3,5),16);
        const b = parseInt(hex.slice(5,7),16);
        return [r,g,b];
    }
    function cellBg(hex) {
        const [r,g,b] = hexToRgb(hex);
        return `rgba(${r},${g},${b},0.13)`;
    }
    function cellText(hex) {
        const [r,g,b] = hexToRgb(hex);
        if (document.documentElement.classList.contains('dark')) {
            // Dark theme: LIGHTEN the group colour toward white for contrast on
            // the dark cell (darkening it like light mode would be unreadable).
            return `rgb(${Math.round(r+(255-r)*0.6)},${Math.round(g+(255-g)*0.6)},${Math.round(b+(255-b)*0.6)})`;
        }
        return `rgb(${Math.round(r*0.55)},${Math.round(g*0.55)},${Math.round(b*0.55)})`;
    }

    // Escapes quotes too: these values land inside double-quoted style/HTML
    // attributes, where a bare `"` would close the attribute early.
    function esc(s) {
        return String(s)
            .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
            .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    }

    const DAY_NAMES = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes'];
    const COLS = 2;
    const NUM_ROWS = 3;
    const FRIDAY = 4;
    const ROW_TIMES = [
        { start: '16:10', end: '17:30' },
        { start: '17:40', end: '19:00' },
        { start: '19:10', end: '20:30' },
    ];

    // Friday runs four overlapping sessions, so each cell carries its own
    // hours instead of sharing a row band. Mirrors core/schedule_utils.py —
    // keep the two in sync.
    //   row 0 · col 0 → infantil     16:30–17:15
    //   row 0 · col 1 → primaria     16:00–17:25
    //   row 1 · col 0 → Fun Friday   17:30–18:30  (fixed label, not assignable)
    //   row 1 · col 1 → adultos      17:30–19:00
    const FRIDAY_TIMES = {
        '0-0': { start: '16:30', end: '17:15' },
        '0-1': { start: '16:00', end: '17:25' },
        '1-0': { start: '17:30', end: '18:30' },
        '1-1': { start: '17:30', end: '19:00' },
    };
    const FUN_FRIDAY_ROW = 1;
    const FUN_FRIDAY_COL = 0;

    function cellTimes(row, day, col) {
        if (day === FRIDAY) return FRIDAY_TIMES[`${row}-${col}`] || null;
        return ROW_TIMES[row] || null;
    }

    // ── Build schedule grid from server slots ──────────────────
    // All slots start null (empty) — only saved DB slots are populated
    const schedule = Array.from({ length: NUM_ROWS }, () => new Array(5 * COLS).fill(null));

    slotsRaw.forEach(s => {
        const colPos = s.day * COLS + s.col;
        schedule[s.row][colPos] = { groupId: s.group_id, start: s.start, end: s.end, row: s.row, day: s.day, col: s.col };
    });

    // Ensure every real slot has an entry so edit mode can show a dropdown.
    // Cells with no session (Friday row 2) stay null and render blank.
    for (let row = 0; row < NUM_ROWS; row++) {
        for (let day = 0; day < 5; day++) {
            for (let col = 0; col < COLS; col++) {
                const times = cellTimes(row, day, col);
                if (!times) continue;  // no session in this cell
                const colPos = day * COLS + col;
                if (!schedule[row][colPos]) {
                    schedule[row][colPos] = { groupId: null, start: times.start, end: times.end, row, day, col };
                }
            }
        }
    }

    // Fun Friday occupies ONE Friday cell (17:30–18:30) rather than spanning
    // the row, so the adjacent cell is free for the 17:30–19:00 adult group.
    schedule[FUN_FRIDAY_ROW][FRIDAY * COLS + FUN_FRIDAY_COL] = {
        isFunFriday: true,
        start: FRIDAY_TIMES[`${FUN_FRIDAY_ROW}-${FUN_FRIDAY_COL}`].start,
        end: FRIDAY_TIMES[`${FUN_FRIDAY_ROW}-${FUN_FRIDAY_COL}`].end,
    };

    // ── CSRF helper ─────────────────────────────────────────────
    function getCsrf() {
        return document.querySelector('[name=csrfmiddlewaretoken]')?.value
            || document.cookie.split(';').map(c=>c.trim()).find(c=>c.startsWith('csrftoken='))?.split('=')[1]
            || '';
    }

    // ── Save slot via API ───────────────────────────────────────
    function saveSlot(row, day, col, groupId) {
        fetch('/api/schedule/slot/save/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
            body: JSON.stringify({ row, day, col, group_id: groupId }),
        });
    }

    // Friday cells carry their own hours (the row gutter only holds the
    // Mon–Thu band), so stamp them into the cell itself.
    function fridayTimeLabel(cell, dayIdx) {
        if (dayIdx !== FRIDAY || !cell || !cell.start) return '';
        return '<div style="font-size:10.5px;font-weight:600;color:var(--sched-dim);line-height:1.2;margin-bottom:2px;">'
            + esc(cell.start) + '–' + esc(cell.end) + '</div>';
    }

    // ── Render a single cell ────────────────────────────────────
    function renderCell(td, cell, row, dayIdx, colIdx) {
        td.innerHTML = '';
        td.style.background = '';
        if (!cell || cell.isFunFriday) return;

        if (scheduleEditMode) {
            td.style.background = 'var(--sched-surface)';
            const sel = document.createElement('select');
            // Theme-aware surface + explicit text colour. `background:#fff` with
            // no colour was unreadable in dark mode: theme.css rewrites the
            // white background to a dark surface but the inherited text stayed
            // dark, so the dropdown rendered dark-on-dark.
            sel.style.cssText = 'width:100%;font-size:0.7rem;padding:4px 2px;border:1px solid var(--sched-border);'
                + 'border-radius:4px;background:var(--sched-surface);color:var(--sched-strong);cursor:pointer;';
            if (dayIdx === FRIDAY) td.insertAdjacentHTML('afterbegin', fridayTimeLabel(cell, dayIdx));
            const blank = document.createElement('option');
            blank.value = '';
            blank.textContent = '— sin grupo —';
            sel.appendChild(blank);
            groups.forEach(g => {
                const opt = document.createElement('option');
                opt.value = g.id;
                opt.textContent = g.name;
                if (cell.groupId === g.id) opt.selected = true;
                sel.appendChild(opt);
            });
            sel.addEventListener('change', function() {
                const gid = this.value ? parseInt(this.value) : null;
                cell.groupId = gid;
                saveSlot(row, dayIdx, colIdx, gid);
                renderDropdowns();
            });
            td.appendChild(sel);
        } else {
            const timeLabel = fridayTimeLabel(cell, dayIdx);
            if (cell.groupId && groupById[cell.groupId]) {
                const g = groupById[cell.groupId];
                td.style.background = cellBg(g.color);
                const dot = `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${esc(g.color)};margin-right:3px;vertical-align:middle;flex-shrink:0;"></span>`;
                td.innerHTML = timeLabel + `<div style="display:flex;align-items:center;justify-content:center;gap:2px;">${dot}<span style="font-size:0.936rem;font-weight:600;color:${esc(cellText(g.color))};">${esc(g.name)}</span></div>`;
            } else {
                // Empty slot — hint to use edit mode
                td.innerHTML = timeLabel + '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;opacity:0.35;"><span style="font-size:11.7px;font-weight:600;color:var(--sched-ink);line-height:1.2;">SIN GRUPO</span><span style="font-size:11.7px;color:var(--sched-dim);line-height:1.2;">editar ✏</span></div>';
            }
        }
    }

    // ── Render full table ───────────────────────────────────────
    function renderTable() {
        const headerRow = document.getElementById('day-header-row');
        const tbody = document.getElementById('schedule-body');

        // Build header if not built yet
        if (headerRow.children.length === 1) {
            DAY_NAMES.forEach((name, i) => {
                const th = document.createElement('th');
                th.colSpan = COLS;
                th.className = 'bg-neutral-50 border-b border-neutral-200 p-3 text-center';
                if (i < 4) th.classList.add('border-r');
                th.style.width = 'calc((100% - 56px) / 5)';
                th.innerHTML = '<span style="font-size:1.1375rem;font-weight:600;color:var(--sched-strong);">' + name + '</span>';
                headerRow.appendChild(th);
            });
        }

        tbody.innerHTML = '';

        for (let row = 0; row < NUM_ROWS; row++) {
            const tr = document.createElement('tr');

            // Time cell
            const timeTd = document.createElement('td');
            timeTd.style.cssText = 'width:56px;min-width:56px;max-width:56px;padding:8px 6px;text-align:right;vertical-align:middle;background:var(--sched-surface);position:sticky;left:0;z-index:10;';
            if (row < NUM_ROWS - 1) timeTd.style.borderBottom = '1px solid var(--sched-border)';
            timeTd.style.borderRight = '1px solid var(--sched-border)';
            timeTd.innerHTML =
                '<span style="display:block;font-size:14.3px;font-weight:600;color:var(--sched-ink);line-height:1.2;">' + ROW_TIMES[row].start + '</span>' +
                '<span style="display:block;font-size:13px;color:var(--sched-dim);line-height:1.2;margin-top:2px;">' + ROW_TIMES[row].end + '</span>';
            tr.appendChild(timeTd);

            let c = 0;
            while (c < 5 * COLS) {
                const dayIdx = Math.floor(c / COLS);
                const colIdx = c % COLS;
                const isLastColInDay = colIdx === COLS - 1;
                const isLastDay = dayIdx === 4;

                const td = document.createElement('td');
                td.style.cssText = 'text-align:center;vertical-align:middle;padding:8px 4px;';
                if (row < NUM_ROWS - 1) td.style.borderBottom = '1px solid var(--sched-border)';
                if (isLastColInDay && !isLastDay) td.style.borderRight = '1px solid var(--sched-border)';

                const cell = schedule[row][c];

                if (cell && cell.isFunFriday) {
                    // Occupies a single cell now, leaving the neighbouring
                    // Friday cell free for the 17:30–19:00 adult group.
                    td.style.background = FF_CLR.bg;
                    td.style.padding = '8px 4px 4px 4px';
                    td.innerHTML = fridayTimeLabel(cell, dayIdx)
                        + '<span style="font-size:0.975rem;font-weight:600;color:' + FF_CLR.text + ';">Fun Friday</span>';
                    tr.appendChild(td);
                    c++;
                    continue;
                }

                renderCell(td, cell, row, dayIdx, colIdx);
                tr.appendChild(td);
                c++;
            }
            tbody.appendChild(tr);
        }
    }

    // Expose renderTable globally so toggleEditMode (defined before DOMContentLoaded) can call it
    window._scheduleRenderTable = renderTable;
    renderTable();

    // Re-render when the light/dark theme toggles so group-name colours (which
    // are computed per-theme in cellText) update immediately without a reload.
    let _lastDark = document.documentElement.classList.contains('dark');
    new MutationObserver(() => {
        const isDark = document.documentElement.classList.contains('dark');
        if (isDark !== _lastDark) {
            _lastDark = isDark;
            renderTable();
            if (typeof renderDropdowns === 'function') renderDropdowns();
        }
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

    // ── Day detail dropdowns ────────────────────────────────────
    function renderDropdowns() {
        const dc = document.getElementById('day-dropdowns');
        dc.innerHTML = '';

        DAY_NAMES.forEach((dayName, dayIdx) => {
            const dayBlocks = [];
            for (let row = 0; row < NUM_ROWS; row++) {
                for (let col = 0; col < COLS; col++) {
                    const cell = schedule[row][dayIdx * COLS + col];
                    if (cell && !cell.isFunFriday && cell.groupId) dayBlocks.push({ row, col, ...cell });
                }
            }
            if (dayBlocks.length === 0) return;

            const sid = 'day-s-' + dayIdx;
            const iid = 'day-i-' + dayIdx;

            let cards = '';
            dayBlocks.forEach(b => {
                const g = groupById[b.groupId];
                if (!g) return;
                const stuHtml = g.students.length > 0
                    ? g.students.map(n => '<span>' + esc(n) + '</span>').join('<span style="color:var(--sched-dim);margin:0 3px;">·</span>')
                    : '<span style="color:var(--sched-dim);font-style:italic;">Sin estudiantes</span>';

                cards += '<div style="display:flex;align-items:flex-start;gap:12px;padding:12px 0;">' +
                    '<div style="width:4px;align-self:stretch;border-radius:9999px;background:' + esc(g.color) + ';flex-shrink:0;"></div>' +
                    '<div style="flex:1;min-width:0;">' +
                        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">' +
                            '<span style="font-size:1.1375rem;font-weight:600;color:' + esc(cellText(g.color)) + ';">' + esc(g.name) + '</span>' +
                            '<span style="font-size:14.3px;color:var(--sched-dim);font-weight:500;">' + b.start + ' - ' + b.end + '</span>' +
                            '<span style="font-size:14.3px;color:var(--sched-dim);">·</span>' +
                            '<span style="font-size:14.3px;color:var(--sched-ink);">' + esc(g.teacher) + '</span>' +
                        '</div>' +
                        '<div style="font-size:11px;color:var(--sched-dim);margin-top:4px;line-height:1.6;">' + stuHtml + '</div>' +
                    '</div></div>';
            });

            const w = document.createElement('div');
            w.className = 'bg-white rounded-lg shadow-lg overflow-hidden';
            w.innerHTML =
                '<button type="button" class="day-toggle w-full px-6 py-4 flex justify-between items-center bg-primary-50 hover:bg-primary-100 transition-colors duration-200 focus:outline-none" data-section="' + sid + '" data-icon="' + iid + '">' +
                    '<h3 class="text-lg font-semibold text-primary-700">' + dayName + '</h3>' +
                    '<span id="' + iid + '" class="material-symbols-outlined text-primary-600" style="transition:transform 0.2s;">expand_more</span>' +
                '</button>' +
                '<div id="' + sid + '" class="hidden">' +
                    '<div style="padding:24px;border-top:1px solid #f3e8ff;">' + cards + '</div>' +
                '</div>';
            dc.appendChild(w);
        });

        // Fun Friday dropdown
        (function () {
            const sid = 'ff-section';
            const iid = 'ff-icon';
            let list = '';
            allStudents.forEach(st => {
                list += '<div style="display:flex;align-items:center;gap:12px;padding:8px 0;">' +
                    '<input type="checkbox" class="ff-checkbox" style="width:14px;height:14px;accent-color:#8b5cf6;cursor:pointer;">' +
                    '<span style="font-size:0.875rem;color:var(--sched-strong);">' + esc(st.first_name) + ' ' + esc(st.last_name) + '</span>' +
                '</div>';
            });
            if (!allStudents.length) {
                list = '<p style="font-size:0.875rem;color:var(--sched-dim);padding:12px 0;text-align:center;">No hay estudiantes activos</p>';
            }

            const w = document.createElement('div');
            w.className = 'bg-white rounded-lg shadow-lg overflow-hidden';
            w.innerHTML =
                '<button type="button" class="day-toggle w-full px-6 py-4 flex justify-between items-center bg-primary-50 hover:bg-primary-100 transition-colors duration-200 focus:outline-none" data-section="' + sid + '" data-icon="' + iid + '">' +
                    '<h3 class="text-lg font-semibold text-primary-700">Fun Friday</h3>' +
                    '<span id="' + iid + '" class="material-symbols-outlined text-primary-600" style="transition:transform 0.2s;">expand_more</span>' +
                '</button>' +
                '<div id="' + sid + '" class="hidden">' +
                    '<div style="padding:24px;border-top:1px solid #f3e8ff;">' + list + '</div>' +
                '</div>';
            dc.appendChild(w);
        })();

        // Toggle logic
        document.querySelectorAll('.day-toggle').forEach(btn => {
            btn.addEventListener('click', function () {
                const section = document.getElementById(this.dataset.section);
                const icon = document.getElementById(this.dataset.icon);
                section.classList.toggle('hidden');
                icon.style.transform = section.classList.contains('hidden') ? '' : 'rotate(180deg)';
            });
        });

        // Checkbox strike-through
        document.addEventListener('change', function (e) {
            if (!e.target.matches('.ff-checkbox')) return;
            const span = e.target.nextElementSibling;
            if (e.target.checked) { span.style.textDecoration = 'line-through'; span.style.color = 'var(--sched-dim)'; }
            else { span.style.textDecoration = ''; span.style.color = 'var(--sched-strong)'; }
        });
    }

    renderDropdowns();
});
