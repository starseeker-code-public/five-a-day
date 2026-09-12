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

    /* ── Local calendar date (YYYY-MM-DD) ───────────────────────────────────
       NOT `new Date().toISOString().split('T')[0]`, which is the UTC date:
       between local midnight and 01:00/02:00 (Madrid is UTC+1/+2) it returns
       YESTERDAY. A dashboard "Hoy" todo created at 00:30 was therefore due
       yesterday and never appeared under "vence hoy", and recording a cash
       payment just after midnight on the 1st booked the money into the
       previous — already reported — month. One primitive for the whole app,
       next to escapeHtml; payments.js and home.js both read it from here. */
    function localDateISO(d) {
        d = d || new Date();
        const p = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    }
    window.localDateISO = localDateISO;

    /* ── fetch(): status checking ───────────────────────────────────────────
       Most of the app's fetch() calls used to read `.json()` straight off the
       response, so the one failure users actually hit — an expired session,
       which answers 403 (CsrfViewMiddleware) or redirects to /login/ and
       returns HTML that JSON.parse chokes on — arrived at the generic
       `.catch()` and was reported as "Error de conexión". That is both wrong
       and unactionable. These helpers separate the three cases and never leak
       exception text to the user (see CLAUDE.md). */
    const MSG_SESSION = 'Tu sesión ha caducado. Recarga la página e inicia sesión de nuevo.';
    const MSG_SERVER = 'El servidor ha devuelto un error. Vuelve a intentarlo en unos momentos.';
    const MSG_NETWORK = 'Error de conexión. Comprueba tu conexión e inténtalo de nuevo.';
    const MSG_THROTTLE = 'Demasiados intentos. Prueba de nuevo en unos minutos.';

    function apiError(message, status) {
        const err = new Error(message);
        err.userMessage = message;
        err.status = status || 0;
        return err;
    }

    // fetch + status check + JSON parse. Adds the CSRF header for unsafe
    // methods from window.CSRF_TOKEN (hidden input first — see above). A
    // FormData body is left without a Content-Type so the browser can set the
    // multipart boundary.
    //
    // Resolves with the parsed JSON body; rejects with `.userMessage` set. An
    // error response that CARRIES a JSON `message`/`error` keeps it: that text
    // is the server's own, written for a human (form validation, "la
    // contraseña actual no es correcta"), and several endpoints answer 400/403
    // with exactly that. Only when there is no such text does the HTTP status
    // decide, which is what turns a bare CSRF 403 (an HTML body) into "sesión
    // caducada" instead of "Error de conexión".
    function apiFetch(url, options) {
        const opts = Object.assign({}, options || {});
        const method = (opts.method || 'GET').toUpperCase();
        const headers = Object.assign({}, opts.headers || {});
        if (method !== 'GET' && method !== 'HEAD') {
            if (!headers['X-CSRFToken']) headers['X-CSRFToken'] = window.CSRF_TOKEN;
            const isFormData = typeof FormData !== 'undefined' && opts.body instanceof FormData;
            if (!isFormData && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
        }
        opts.headers = headers;
        return fetch(url, opts)
            .then(
                function (r) { return r.text().then(function (text) { return { r: r, text: text }; }); },
                function () {
                    // Rejected before a response existed: the request never landed.
                    throw apiError(MSG_NETWORK, 0);
                }
            )
            .then(function (res) {
                const r = res.r;
                let body = null;
                let parsed = false;
                try { body = JSON.parse(res.text); parsed = true; } catch (e) { /* not JSON */ }
                if (r.ok) {
                    if (!parsed) throw apiError(MSG_SERVER, r.status);
                    return body;
                }
                const server = parsed && body ? (body.message || body.error) : null;
                if (typeof server === 'string' && server) throw apiError(server, r.status);
                if (r.status === 401 || r.status === 403) throw apiError(MSG_SESSION, r.status);
                if (r.status === 429) throw apiError(MSG_THROTTLE, r.status);
                throw apiError(MSG_SERVER, r.status);
            });
    }

    // The message to show for any rejection out of apiFetch (or a raw fetch
    // chain). Never the exception text.
    function apiErrorMessage(err) {
        return (err && err.userMessage) || MSG_NETWORK;
    }

    // Three exports, all of them read: apiFetch (21 call sites), apiErrorMessage
    // (every .catch) and API_MESSAGES (the two places that must keep a raw
    // fetch — the multipart enrollment POST, and the parent portal, which does
    // not load this file's page shell). A `checkResponse(response)` wrapper
    // lived here too and nothing called it; window.CSRF_TOKEN was in exactly
    // that state before this change, which is how six duplicate readers of it
    // came to exist.
    window.apiFetch = apiFetch;
    window.apiErrorMessage = apiErrorMessage;
    window.API_MESSAGES = { session: MSG_SESSION, server: MSG_SERVER, network: MSG_NETWORK, throttle: MSG_THROTTLE };


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
            apiFetch(historyUrl + '?offset=' + offset)
                .then(function (data) {
                    renderEntries(data.entries, append);
                    offset += data.entries.length;
                    if (data.has_more) {
                        loadMoreContainer.classList.remove('hidden');
                    } else {
                        loadMoreContainer.classList.add('hidden');
                    }
                })
                .catch(function (err) {
                    if (!append) {
                        entriesContainer.replaceChildren();
                        const p = document.createElement('div');
                        p.className = 'px-4 py-4 text-center text-sm text-neutral-400';
                        p.textContent = apiErrorMessage(err);
                        entriesContainer.appendChild(p);
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
        // Escape is handled once, generically, by initModalA11y below (which
        // clicks #view-help-close). A page-level "any Escape closes the help"
        // listener here fired even when the dialog was shut.
    }

    /* ── Modal accessibility (focus trap, Escape, restore focus, inert bg) ──
       The app has nine `role="dialog" aria-modal="true"` panels, each opened by
       its own page script. None managed focus: opening one left the caret in
       the page behind it, Tab walked straight out of the dialog into content
       the user cannot see, closing it dropped focus to <body> (so a keyboard
       user restarted from the top of the page), and three had no Escape at all.
       Doing it per-modal is how three of them ended up without it, so it is
       done once here and every present and future aria-modal dialog inherits
       it — no markup change beyond the role/aria-modal pair.

       Visibility is observed rather than hooked, because the openers disagree
       about mechanism: some set `style.display`, others toggle the `hidden` /
       `flex` classes. */
    (function initModalA11y() {
        const SELECTOR = '[role="dialog"][aria-modal="true"]';
        const FOCUSABLE = [
            'a[href]', 'button:not([disabled])', 'input:not([disabled]):not([type="hidden"])',
            'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
        ].join(',');
        // First match wins, so -close/-cancel are listed ahead of anything a
        // dialog might call -confirm: Escape must DISMISS, never commit.
        const CLOSER = '[data-close-modal],[id$="-close"],[id$="-close-btn"],[id$="-cancel"],[id$="-cancel-btn"]';

        let active = null;

        function onScreen(el) {
            return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
        }

        function focusables(el) {
            return Array.prototype.filter.call(el.querySelectorAll(FOCUSABLE), onScreen);
        }

        function deactivate() {
            if (!active) return;
            active.inerted.forEach(function (n) { n.inert = false; });
            const opener = active.opener;
            active = null;
            if (opener && opener.focus && document.contains(opener)) {
                opener.focus({ preventScroll: true });
            }
        }

        function activate(el) {
            if (active && active.el === el) return;
            if (active) deactivate();
            active = {
                el: el,
                opener: document.activeElement,
                // Remember HOW it was shown so a dismissal reverses exactly
                // that: clearing an inline display on a class-toggled dialog
                // (or vice versa) would leave it unable to reopen.
                usedInlineDisplay: el.style.display !== '',
                inerted: [],
            };
            // Make the background inert. Walk from the dialog up to <body> and
            // inert every SIBLING at each level, rather than inert-ing the body's
            // children: several dialogs are nested inside the main content
            // wrapper (expenses.html's edit modal is inside {% block content %}),
            // so a single pass over body.children would leave everything around
            // them reachable. The path itself is never inerted, so the dialog
            // stays live. `inert` is a no-op property on browsers that lack it —
            // the focus trap below is the portable half.
            for (let node = el; node && node !== document.body; node = node.parentElement) {
                const parent = node.parentElement;
                if (!parent) break;
                Array.prototype.forEach.call(parent.children, function (sib) {
                    if (sib === node || sib.inert) return;
                    sib.inert = true;
                    active.inerted.push(sib);
                });
            }
            const f = focusables(el);
            if (f.length) {
                f[0].focus({ preventScroll: true });
            } else {
                el.setAttribute('tabindex', '-1');
                el.focus({ preventScroll: true });
            }
        }

        function dismiss() {
            if (!active) return;
            const el = active.el;
            const usedInline = active.usedInlineDisplay;
            const btn = el.querySelector(CLOSER);
            if (btn) {
                btn.click();          // the page's own closer — keeps its state in sync
            } else if (usedInline) {
                el.style.display = 'none';
            } else {
                el.classList.add('hidden');
                el.classList.remove('flex');
            }
            document.body.style.overflow = '';
            deactivate();
        }

        function watch(el) {
            new MutationObserver(function () {
                if (onScreen(el)) activate(el);
                else if (active && active.el === el) deactivate();
            }).observe(el, { attributes: true, attributeFilter: ['style', 'class'] });
            if (onScreen(el)) activate(el);
        }

        document.querySelectorAll(SELECTOR).forEach(watch);

        document.addEventListener('keydown', function (e) {
            if (!active) return;
            if (e.key === 'Escape') { e.preventDefault(); dismiss(); return; }
            if (e.key !== 'Tab') return;
            const f = focusables(active.el);
            if (!f.length) { e.preventDefault(); return; }
            const first = f[0];
            const last = f[f.length - 1];
            const inside = active.el.contains(document.activeElement);
            if (e.shiftKey && (!inside || document.activeElement === first)) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && (!inside || document.activeElement === last)) {
                e.preventDefault();
                first.focus();
            }
        }, true);
    })();
})();
