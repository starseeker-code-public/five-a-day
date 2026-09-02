/**
 * base.js — Global scripts loaded on every authenticated page.
 * Handles: notification dropdown, history dropdown with lazy-load pagination.
 *
 * Requires data attributes on the page:
 *   <body data-history-url="{% url 'history_list' %}">
 */

(function () {
    'use strict';

    /* ── CSRF helper ──────────────────────────────────────────────────────── */
    function getCookie(name) {
        const cookies = document.cookie.split(';');
        for (let c of cookies) {
            c = c.trim();
            if (c.startsWith(name + '=')) return decodeURIComponent(c.substring(name.length + 1));
        }
        return null;
    }
    // Input FIRST, cookie only as a fallback: CSRF_COOKIE_HTTPONLY is True
    // whenever DEBUG=False, so `document.cookie` is empty in testing/production
    // and a cookie-first reader silently 403s every POST there.
    window.CSRF_TOKEN =
        (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value ||
        getCookie('csrftoken') || '';

    /* ── HTML escaping ────────────────────────────────────────────────────── */
    // History messages embed user-supplied text (todo titles, student names,
    // payment concepts). They are rendered with innerHTML below, so they must
    // be escaped or a todo called `<img src=x onerror=...>` executes in every
    // admin's browser on every page.
    function escapeHtml(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
    window.escapeHtml = escapeHtml;


    /* ── Declarative handlers (CSP) ─────────────────────────────────────────
       Inline on*="" attributes are what forces 'unsafe-inline' into script-src.
       These three delegated listeners replace the recurring patterns:
         data-confirm="msg"        on a <form>  -> confirm() before submit
         data-autosubmit           on a <select> -> submit its form on change
         data-close-modal="id"     on a <button> -> hide that modal + restore scroll
       They are delegated on document so content injected later still works. */
    document.addEventListener('submit', function (e) {
        const msg = e.target && e.target.getAttribute && e.target.getAttribute('data-confirm');
        if (msg && !window.confirm(msg)) {
            e.preventDefault();
        }
    });

    document.addEventListener('change', function (e) {
        const t = e.target;
        if (t && t.matches && t.matches('[data-autosubmit]') && t.form) {
            t.form.submit();
        }
    });

    document.addEventListener('click', function (e) {
        const btn = e.target && e.target.closest ? e.target.closest('[data-close-modal]') : null;
        if (!btn) return;
        const modal = document.getElementById(btn.getAttribute('data-close-modal'));
        if (modal) {
            modal.style.display = 'none';
            document.body.style.overflow = '';
        }
    });

    /* ── Notifications dropdown toggle ────────────────────────────────────── */
    const notifBtn = document.getElementById('notif-btn');
    const notifDropdown = document.getElementById('notif-dropdown');

    if (notifBtn && notifDropdown) {
        notifBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            notifDropdown.classList.toggle('hidden');
            const hd = document.getElementById('history-dropdown');
            if (hd) hd.classList.add('hidden');
        });

        document.addEventListener('click', function () {
            notifDropdown.classList.add('hidden');
        });

        notifDropdown.addEventListener('click', function (e) {
            e.stopPropagation();
        });
    }

    /* ── History dropdown with lazy-load pagination ───────────────────────── */
    const historyBtn = document.getElementById('history-btn');
    const historyDropdown = document.getElementById('history-dropdown');
    const entriesContainer = document.getElementById('history-entries');
    const loadMoreContainer = document.getElementById('history-load-more');
    const loadMoreBtn = document.getElementById('history-more-btn');
    const historyUrl = document.body.dataset.historyUrl || '/api/history/';

    if (historyBtn && historyDropdown) {
        let offset = 0;
        let loaded = false;

        function formatTimeAgo(isoStr) {
            const now = new Date();
            const then = new Date(isoStr);
            const diffMs = now - then;
            const diffMin = Math.floor(diffMs / 60000);
            if (diffMin < 1) return 'ahora';
            if (diffMin < 60) return diffMin + ' min';
            const diffH = Math.floor(diffMin / 60);
            if (diffH < 24) return diffH + 'h';
            const diffD = Math.floor(diffH / 24);
            if (diffD < 30) return diffD + 'd';
            return then.toLocaleDateString('es-ES', { day: '2-digit', month: 'short' });
        }

        function renderEntries(entries, append) {
            if (!append) entriesContainer.innerHTML = '';
            if (entries.length === 0 && !append) {
                entriesContainer.innerHTML =
                    '<div class="px-4 py-8 text-center">' +
                    '<span class="material-symbols-outlined text-neutral-300 text-4xl">history</span>' +
                    '<p class="text-sm text-neutral-400 mt-2">Sin historial todavía</p></div>';
                return;
            }
            entries.forEach(function (e) {
                const div = document.createElement('div');
                div.className = 'px-4 py-3 border-b border-neutral-50 flex items-start gap-3 hover:bg-neutral-50';
                div.innerHTML =
                    '<span class="material-symbols-outlined text-primary-400 shrink-0 text-xl mt-0.5">' + escapeHtml(e.icon) + '</span>' +
                    '<div class="flex-1 min-w-0">' +
                    '<p class="text-neutral-700 text-sm leading-snug break-words">' + escapeHtml(e.message) + '</p>' +
                    '<p class="text-xs text-neutral-400 mt-0.5">' + escapeHtml(formatTimeAgo(e.created_at)) + '</p>' +
                    '</div>';
                entriesContainer.appendChild(div);
            });
        }

        function fetchHistory(append) {
            fetch(historyUrl + '?offset=' + offset)
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    renderEntries(data.entries, append);
                    offset += data.entries.length;
                    if (data.has_more) {
                        loadMoreContainer.classList.remove('hidden');
                    } else {
                        loadMoreContainer.classList.add('hidden');
                    }
                })
                .catch(function () {
                    if (!append) {
                        entriesContainer.innerHTML =
                            '<div class="px-4 py-4 text-center text-sm text-neutral-400">Error al cargar el historial</div>';
                    }
                });
        }

        historyBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            historyDropdown.classList.toggle('hidden');
            const nd = document.getElementById('notif-dropdown');
            if (nd) nd.classList.add('hidden');
            if (!loaded) {
                loaded = true;
                offset = 0;
                fetchHistory(false);
            }
        });

        if (loadMoreBtn) {
            loadMoreBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                fetchHistory(true);
            });
        }

        document.addEventListener('click', function () {
            historyDropdown.classList.add('hidden');
        });

        historyDropdown.addEventListener('click', function (e) {
            e.stopPropagation();
        });
    }

    /* ── Keyboard quick-nav: number keys → sidebar links (data-hotkey) ────── */
    /* Only fires outside text fields and without modifier keys. A hotkey only
       works if its link is present in the DOM (so hidden admin-only links are
       inert for non-admin teachers). */
    document.addEventListener('keydown', function (e) {
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        const t = e.target;
        const tag = t && t.tagName ? t.tagName.toLowerCase() : '';
        if (tag === 'input' || tag === 'textarea' || tag === 'select' || (t && t.isContentEditable)) return;
        if (!/^[0-9]$/.test(e.key)) return;
        const link = document.querySelector('.sidebar-link[data-hotkey="' + e.key + '"]');
        if (link && link.href) { e.preventDefault(); window.location.href = link.href; }
    });

    /* ── Per-view help ("?" button, bottom-left) ──────────────────────────── */
    const helpContent = document.getElementById('view-help-content');
    const helpBtn = document.getElementById('view-help-btn');
    const helpModal = document.getElementById('view-help-modal');
    if (helpBtn && helpModal && helpContent && helpContent.innerHTML.trim() !== '') {
        const body = document.getElementById('view-help-modal-body');
        if (body) body.innerHTML = helpContent.innerHTML;
        helpBtn.style.display = 'flex';  // revealed only when the page provides help
        const closeHelp = () => { helpModal.style.display = 'none'; };
        helpBtn.addEventListener('click', () => { helpModal.style.display = 'flex'; });
        document.getElementById('view-help-close')?.addEventListener('click', closeHelp);
        helpModal.addEventListener('click', (e) => { if (e.target === helpModal) closeHelp(); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeHelp(); });
    }
})();
