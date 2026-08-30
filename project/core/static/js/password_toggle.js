/* password_toggle.js — hold-to-reveal eye button for password inputs.

   Any `<button data-password-toggle="<input id>">` inside the same wrapper
   reveals its input while the button is held down (pointer or keyboard) and
   re-masks it the moment it is released, the pointer leaves, or focus is lost.
   The value is never revealed on a plain click, so a shoulder-surfed password
   cannot stay on screen unattended. */
(function () {
    'use strict';

    var ICON_SHOWN = 'visibility_off';
    var ICON_HIDDEN = 'visibility';

    function setIcon(btn, revealed) {
        var icon = btn.querySelector('.material-symbols-outlined');
        if (icon) icon.textContent = revealed ? ICON_SHOWN : ICON_HIDDEN;
        btn.setAttribute('aria-pressed', revealed ? 'true' : 'false');
    }

    function bind(btn) {
        var input = document.getElementById(btn.getAttribute('data-password-toggle'));
        if (!input) return;

        function reveal(e) {
            if (e) e.preventDefault();   /* keep focus on the input */
            input.type = 'text';
            setIcon(btn, true);
        }

        function mask() {
            input.type = 'password';
            setIcon(btn, false);
        }

        btn.addEventListener('pointerdown', reveal);
        btn.addEventListener('pointerup', mask);
        btn.addEventListener('pointerleave', mask);
        btn.addEventListener('pointercancel', mask);
        btn.addEventListener('blur', mask);
        window.addEventListener('pointerup', mask);
        window.addEventListener('blur', mask);

        /* Keyboard: hold Space/Enter while the button is focused. */
        btn.addEventListener('keydown', function (e) {
            if (e.key === ' ' || e.key === 'Enter') reveal(e);
        });
        btn.addEventListener('keyup', function (e) {
            if (e.key === ' ' || e.key === 'Enter') mask();
        });

        /* Never submit a form with the password still in the clear. */
        if (input.form) input.form.addEventListener('submit', mask);

        setIcon(btn, false);
    }

    function run() {
        var buttons = document.querySelectorAll('[data-password-toggle]');
        for (var i = 0; i < buttons.length; i++) bind(buttons[i]);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', run);
    } else {
        run();
    }
})();
