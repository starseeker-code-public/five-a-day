import { useEffect, useRef } from "react";
import { contactForm, siteConfig } from "../data";

/**
 * The acknowledgement shown once the academy has actually received a message.
 *
 * WHY A DIALOG AND NOT THE OLD INLINE PANEL. The success state used to REPLACE
 * the form, so the confirmation and the form could never be on screen at once:
 * a family who wanted to send a second message — a correction, a second child —
 * had no way back except reloading the page. A dialog sits on top, the form is
 * reset underneath it, and closing returns them to a blank form.
 *
 * It is deliberately NOT an email to the address typed in the box. That address
 * is unverified, so an acknowledgement mail is a way to make this origin send
 * mail to a stranger on a stranger's say-so; the confirmation belongs where the
 * person actually is.
 *
 * ACCESSIBILITY is the whole reason this is a component rather than a styled
 * div. A modal that only LOOKS modal is worse than none: focus stays behind it,
 * so a screen-reader user is told nothing happened and a keyboard user tabs
 * into a form they can no longer see. Hence `role="dialog"` + `aria-modal`, an
 * accessible name wired through `aria-labelledby`, focus moved in on open and
 * restored to the trigger on close, Escape to dismiss, and a tab loop.
 */
export default function ContactSuccessDialog({ open, visible, onClose }) {
  const panelRef = useRef(null);
  const closeRef = useRef(null);

  // Focus moves INTO the dialog on open and back to whatever opened it on
  // close. Without the restore, dismissing drops focus onto <body> and the
  // next Tab starts from the top of the page — the family is silently sent
  // back to the navbar instead of to the form they were just using.
  useEffect(() => {
    if (!open) return undefined;
    const previous = document.activeElement;
    closeRef.current?.focus();
    return () => {
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, [open]);

  // The page must not scroll behind an open modal: on a phone the backdrop
  // covers the viewport, so scrolling moves content the user cannot see and
  // leaves them somewhere else entirely once the dialog closes.
  useEffect(() => {
    if (!open) return undefined;
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = overflow;
    };
  }, [open]);

  // Escape closes; Tab is trapped. The trap is a loop over the panel's own
  // focusable nodes rather than a library: there are three of them and they
  // are all in this file.
  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = panelRef.current?.querySelectorAll("a[href], button:not([disabled])");
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      // z-[60], not z-50: the navbar and the WhatsApp popup are BOTH z-50, so
      // with an equal index the painting order falls to DOM order — and Layout
      // renders the popup after the page, so it drew on top of this dialog and
      // its backdrop. On a phone it landed squarely over the buttons.
      className={`fixed inset-0 z-[60] flex items-center justify-center p-4 bg-primary-darker/70 modal-backdrop ${
        visible ? "modal-backdrop-visible" : ""
      }`}
      // Clicking the backdrop dismisses, but only the backdrop itself — the
      // check stops a click that started inside the panel (selecting the text
      // of the phone number, say) from closing it on mouse-up.
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="contact-success-title"
        aria-describedby="contact-success-body"
        className={`w-full max-w-md bg-white rounded-2xl shadow-2xl p-8 text-center modal-panel ${
          visible ? "modal-panel-visible" : ""
        }`}
      >
        {/* The tick is decorative: the heading below already says the same
            thing, and an alt text here would have a screen reader announce it
            twice. */}
        <div
          aria-hidden="true"
          className="mx-auto mb-5 w-16 h-16 rounded-full bg-accent-green flex items-center justify-center"
        >
          <svg className="w-8 h-8 text-primary-darker" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
          </svg>
        </div>

        <h2 id="contact-success-title" className="font-title text-2xl font-bold text-primary-darker mb-3">
          {contactForm.successTitle}
        </h2>
        <p id="contact-success-body" className="text-primary-darker/70 leading-relaxed mb-2">
          {contactForm.successBody}
        </p>
        <p className="text-primary-darker/60 text-sm leading-relaxed mb-6">{contactForm.successFollowUp}</p>

        <div className="flex flex-col gap-3">
          <a
            href={siteConfig.whatsapp}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center justify-center gap-2 bg-whatsapp hover:bg-whatsapp-dark text-white font-heading font-bold py-3 px-8 rounded-md transition-all duration-200 hover:-translate-y-0.5"
          >
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
            </svg>
            {contactForm.successWhatsappLabel}
          </a>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="text-primary-darker/60 hover:text-primary-darker font-heading font-semibold py-2 transition-colors"
          >
            {contactForm.successCloseLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
