/**
 * theme.js — light/dark theme toggle.
 *
 * The initial theme class is applied by an inline no-flash script in
 * base.html's <head> (before first paint). This file only wires the header
 * toggle button to flip and persist the choice in localStorage.
 */
(function () {
    'use strict';

    const btn = document.getElementById('theme-toggle-btn');
    if (!btn) return;

    btn.addEventListener('click', function () {
        const isDark = document.documentElement.classList.toggle('dark');
        try {
            localStorage.setItem('theme', isDark ? 'dark' : 'light');
        } catch (e) {
            /* localStorage unavailable (private mode) — toggle still works for the session */
        }
    });
})();
