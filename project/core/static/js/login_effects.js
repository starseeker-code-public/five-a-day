/* login_effects.js — text & button animations for the login card */
(function () {
    'use strict';

    /* ── Inject all animation CSS ───────────────────────────── */
    var styleEl = document.createElement('style');
    styleEl.textContent =

        /* Bienvenida: quill reveal left-to-right */
        '@keyframes bridgertonBloom {' +
        '  0%   { opacity:0; clip-path:inset(0 100% 0 0); filter:blur(5px); }' +
        '  18%  { opacity:1; }' +
        '  100% { opacity:1; clip-path:inset(0 0% 0 0); filter:blur(0); }' +
        '}' +
        '.fx-heading { animation:bridgertonBloom 1.4s cubic-bezier(.23,1,.32,1) both; }' +

        /* Quote: fast spring reveal */
        '@keyframes quoteReveal {' +
        '  from { opacity:0; transform:translateY(8px); }' +
        '  to   { opacity:1; transform:translateY(0); }' +
        '}' +
        '.fx-quote { animation:quoteReveal .3s cubic-bezier(.34,1.56,.64,1) both; }' +

        /* Quote: violet glow pulse */
        '@keyframes quoteGlow {' +
        '  0%   { text-shadow:0 0 0 rgba(139,92,246,0); }' +
        '  35%  { text-shadow:0 0 14px rgba(139,92,246,.55),0 0 30px rgba(124,58,237,.18); }' +
        '  100% { text-shadow:0 0 0 rgba(139,92,246,0); }' +
        '}' +
        '.fx-quote-glow { animation:quoteGlow 1.4s ease-in-out; }' +

        '';

    document.head.appendChild(styleEl);

    /* ── Timing (ms) ────────────────────────────────────────── */
    var HEADING_MS  = 1400;   // bridgertonBloom duration
    var QUOTE_MS    = 300;    // quoteReveal duration
    var QUOTE_DELAY = 1000;   // quote appears 1s after page load

    /* ── Sequence ───────────────────────────────────────────── */
    function run() {
        var heading = document.querySelector('.card-heading');
        var quote   = document.getElementById('login-quote');

        /* Step 1 — Bienvenida, immediately on load */
        if (heading) heading.classList.add('fx-heading');

        /* Step 2 — Quote, right after Bienvenida finishes */
        setTimeout(function () {
            if (!quote) return;
            quote.classList.add('fx-quote');

            /* Quote glow fires once the reveal completes */
            setTimeout(function () {
                if (!quote) return;
                quote.style.opacity = '1';
                quote.classList.add('fx-quote-glow');
            }, QUOTE_MS);

        }, QUOTE_DELAY);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', run);
    } else {
        run();
    }
})();
