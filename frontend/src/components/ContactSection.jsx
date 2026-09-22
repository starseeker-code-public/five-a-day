import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { contactForm, siteConfig } from "../data";
import ContactSuccessDialog from "./ContactSuccessDialog";

// Django serves this site now, so the form posts to our own endpoint instead
// of to Netlify Forms (which handled a POST to "/" while the site was deployed
// there, and which simply does not exist here). See core/views/frontend.py.
const CONTACT_ENDPOINT = "/api/contact/";

// The CSRF token, from the <meta> tag Django injects into this document
// (core/views/frontend.py::_inject_csrf_meta) — NOT from document.cookie.
// `CSRF_COOKIE_HTTPONLY` is True whenever DEBUG is off, so a cookie reader
// returns "" on the testing VM and in production and every submission is
// refused with a 403 the handler below can only report as a generic failure.
// It works in development, which is exactly why it reached production.
// The cookie stays as a fallback for a stale cached shell served without the tag.
function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta?.content) return meta.content;
  const match = document.cookie.match(/(^| )csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[2]) : "";
}

// How many times a REJECTED fetch is tried again. A rejection means no HTTP
// response arrived at all — the request died in transport — which is a
// different thing from the server refusing it, and the only one worth
// repeating.
//
// WHY THIS EXISTS. The QA VM runs Gunicorn's sync worker with nothing in front
// of it, so it answers `Connection: close` and every request is a fresh TCP
// connection. A POST that loses the race with that teardown comes back as a
// reset, and a browser will silently re-drive an idempotent GET but never a
// POST — so it reached the family as "comprueba tu conexión" on a site that
// was up, healthy, and answering everything else. The enquiry is then lost
// with no trace on EITHER side: nothing reached Django, so the academy never
// learns that anybody tried. That is the failure this exists to close, and it
// is the same shape as the one the response check above closed — a form that
// delivers nothing while looking to the family like it behaved.
//
// WHY RETRYING A POST IS SAFE HERE, though a POST is not idempotent. The
// endpoint opens a 60-second per-client cooldown AFTER the mail goes out
// (`begin_cooldown`, at the bottom of submit_contact_form). So the one case
// that could deliver twice — the first attempt DID reach the view and only its
// response was lost — meets that cooldown and is answered 429, which the
// handler below already explains in the academy's own words. The
// de-duplication window is not something this retry adds; it is something it
// leans on, so do not remove the cooldown without revisiting this.
//
// Tried again IMMEDIATELY, and only once. The failure is a teardown race and a
// brand-new connection is exactly what settles it, so a delay buys nothing —
// while the restarts a delay WOULD cover (the nightly testing deploy) last
// minutes, which no client-side retry can sit through.
const TRANSPORT_RETRIES = 1;

async function postContact(body) {
  let lastError;
  for (let attempt = 0; attempt <= TRANSPORT_RETRIES; attempt += 1) {
    try {
      return await fetch(CONTACT_ENDPOINT, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          // Re-read per attempt rather than captured once: it costs nothing,
          // and it keeps the token and the request carrying it minted together.
          "X-CSRFToken": getCsrfToken(),
        },
        body,
      });
    } catch (err) {
      // Only a transport rejection lands here. Anything the server actually
      // ANSWERED — 400, 403, 502 — is returned above and never retried: those
      // are decisions, and repeating one either replays a refusal the server
      // has already made or, for the 502 that means the SMTP send failed,
      // attempts a second delivery with no cooldown yet set to stop it.
      lastError = err;
    }
  }
  // Budget exhausted. Re-thrown so handleSubmit's catch still owns the message
  // the family reads, and there is exactly one place that wording lives.
  throw lastError;
}

export default function ContactSection() {
  const [formData, setFormData] = useState({});
  const [submitted, setSubmitted] = useState(false);
  // Mounted vs. animated-in, kept apart so the panel can transition FROM its
  // hidden state: setting both in one render would paint it already open and
  // there would be nothing to animate. Same split as WhatsAppPopup.
  const [dialogVisible, setDialogVisible] = useState(false);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  // Seconds left before another message may be sent. The SERVER enforces this
  // (core/views/frontend.py, CONTACT_COOLDOWN_SECONDS); the countdown exists so
  // somebody who double-clicks sees why the button is inert instead of meeting
  // a 429 they did not ask for.
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (!submitted) return undefined;
    const id = setTimeout(() => setDialogVisible(true), 20);
    return () => clearTimeout(id);
  }, [submitted]);

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const id = setTimeout(() => setCooldown((left) => left - 1), 1000);
    return () => clearTimeout(id);
  }, [cooldown]);

  const closeDialog = () => {
    setDialogVisible(false);
    // Unmount only once the fade-out has run; the duration matches the
    // transition in styles.css (section 11).
    setTimeout(() => setSubmitted(false), 250);
  };

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setSending(true);
    try {
      const payload = new FormData(e.target);
      // Serialised ONCE, outside the retry: the body must not be re-read from
      // a form the visitor may have started editing between two attempts.
      const response = await postContact(new URLSearchParams(payload).toString());
      // The status is CHECKED. The previous version set `submitted` inside the
      // try regardless, so any failure still told the family "mensaje enviado"
      // while the academy received nothing — the worst of both outcomes,
      // because nobody follows up on a message they believe was delivered.
      const body = await response.json().catch(() => null);
      if (!response.ok || !body?.success) {
        // A throttled request is answered by the rate limiter itself, which
        // replies text/plain — so `body` is null and the generic message would
        // blame the send rather than explain the wait. The status decides here,
        // the same way base.js does it for the management app.
        if (response.status === 429) {
          setError(body?.error || contactForm.throttleMessage);
          setCooldown(contactForm.cooldownSeconds);
          return;
        }
        setError(
          body?.error ||
            "No hemos podido enviar el mensaje. Inténtalo de nuevo o escríbenos por WhatsApp.",
        );
        return;
      }
      setSubmitted(true);
      setCooldown(contactForm.cooldownSeconds);
      // Clearing the state IS clearing the boxes, because the fields below are
      // controlled. That mattered the moment success stopped unmounting the
      // form: `formData` was written on every keystroke and never read, so the
      // fields kept their text behind the dialog.
      //
      // The obvious fix — `form.reset()` — is WRONG here and fails in a way no
      // amount of looking at the page reveals. React keeps its own tracker of
      // each input's last value; a native reset changes the DOM without going
      // through React, so the tracker still holds the old text and the next
      // keystroke replays it: typing "Ana" into a visibly EMPTY box yields
      // "AnaAna". Binding `value` keeps React the only writer and the question
      // cannot arise.
      setFormData({});
    } catch {
      setError(
        "No hemos podido enviar el mensaje. Comprueba tu conexión o escríbenos por WhatsApp.",
      );
    } finally {
      setSending(false);
    }
  };

  const inputClass =
    "w-full border border-white/30 bg-white/10 text-white placeholder-white/40 rounded-md px-4 py-3 text-base focus:outline-none focus:ring-2 focus:ring-accent-green focus:border-transparent";

  return (
    <section id="contacto" className="py-12 bg-white">
      <div className="max-w-[980px] mx-auto px-4">
        {/* Purple rectangle container */}
        <div className="bg-contact rounded-2xl p-6 md:p-10">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8 md:gap-12">

            {/* Left column — academy info */}
            <div className="flex flex-col items-center md:items-start text-white">
              {/* 1. Logo */}
              <img
                src="/images/logo_transparent.png"
                alt="Logotipo de Five a Day English Academy, Albacete"
                loading="lazy"
                className="h-44 w-auto mb-4 shadow-logo"
              />

              {/* 2. Contact text */}
              <div className="space-y-3 text-sm w-full mb-1">
                <div className="flex items-start gap-3">
                  <svg className="w-5 h-5 shrink-0 mt-0.5 text-accent-green" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z" />
                  </svg>
                  <a href={siteConfig.whatsapp} target="_blank" rel="noopener noreferrer" className="hover:text-accent-green transition-colors">
                    {siteConfig.phone}
                  </a>
                </div>

                <div className="flex items-start gap-3">
                  <svg className="w-5 h-5 shrink-0 mt-0.5 text-accent-green" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                  </svg>
                  <a href={`mailto:${siteConfig.email}`} className="hover:text-accent-green transition-colors break-all">
                    {siteConfig.email}
                  </a>
                </div>

                <div className="flex items-start gap-3">
                  <svg className="w-5 h-5 shrink-0 mt-0.5 text-accent-green" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                  </svg>
                  <a href={siteConfig.mapsUrl} target="_blank" rel="noopener noreferrer" className="hover:text-accent-green transition-colors">
                    <span className="text-accent-yellow font-semibold">{siteConfig.address.street}</span>
                    {", "}{siteConfig.address.postalCode} {siteConfig.address.city}, {siteConfig.address.country}
                  </a>
                </div>

                <div className="flex items-start gap-3">
                  <svg className="w-5 h-5 shrink-0 mt-0.5 text-accent-green" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <div>
                    <p>{siteConfig.hours.phone}</p>
                    <p>{siteConfig.hours.inPerson}</p>
                  </div>
                </div>
              </div>

              {/* 3 & 4. logo_contact + subvencion — same size, right-aligned, tight spacing */}
              <div className="flex flex-col self-center -mt-12">
                <img
                  src="/images/logo_contact.png"
                  alt="Five a Day English Academy"
                  className="h-60 w-60 object-contain shadow-image"
                />
                <img
                  src="/images/subvencion.png"
                  alt="Sello de subvención recibida por Five a Day English Academy, Albacete"
                  loading="lazy"
                  className="h-60 w-60 object-contain -mt-16 shadow-image"
                />
              </div>
            </div>

            {/* Right column — form or maintenance message */}
            <div>
              <h2 className="font-title text-2xl font-bold text-white mb-1">{contactForm.title}</h2>
              <p className="text-white/60 text-base mb-5">{contactForm.subtitle}</p>

              {contactForm.maintenanceActive ? (
                <div className="bg-accent-yellow/20 border-2 border-accent-yellow text-white p-8 rounded-lg text-center">
                  <p className="text-lg font-heading font-semibold text-accent-yellow mb-4">{contactForm.maintenanceTitle}</p>
                  <p className="text-white/90 mb-6 leading-relaxed">{contactForm.maintenanceMessage}</p>
                  <a
                    href={siteConfig.whatsapp}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 bg-whatsapp hover:bg-whatsapp-dark text-white font-heading font-bold py-3 px-8 rounded-md transition-all duration-200 hover:-translate-y-0.5"
                  >
                    <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
                    </svg>
                    Enviar WhatsApp
                  </a>
                </div>
              ) : (
                <form name="contact" method="POST" onSubmit={handleSubmit} className="space-y-4">
                  {/* Honeypot: no human sees this, so anything in it is a bot.
                      Django reads it (HONEYPOT_FIELD) and discards the
                      submission silently. The `netlify` attribute and the
                      `form-name` input that used to sit here were Netlify
                      Forms' wiring and mean nothing to Django. */}
                  <input
                    type="text"
                    name="bot-field"
                    tabIndex={-1}
                    autoComplete="off"
                    aria-hidden="true"
                    // Inline style as well as the class: the class only hides
                    // this if the Tailwind bundle loaded. If it ever does not,
                    // a class-only trap becomes a VISIBLE field that real
                    // people fill in — and Django discards every submission
                    // that has it set, so their message would vanish.
                    style={{ display: "none" }}
                    className="hidden"
                  />
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    {contactForm.fields.filter(f => f.type !== "textarea").map((field) => {
                      if (field.type === "select") {
                        return (
                          <div key={field.name}>
                            {/* htmlFor/id: without the pairing the label is
                                decorative text — a screen reader announces an
                                unnamed control and clicking the label does not
                                focus it. */}
                            <label htmlFor={field.name} className="block text-sm font-medium text-white/70 mb-1">
                              {field.label}
                            </label>
                            <select
                              id={field.name}
                              name={field.name}
                              value={formData[field.name] ?? ""}
                              onChange={handleChange}
                              className={inputClass}
                            >
                              <option value="">Seleccionar...</option>
                              {field.options.map((opt) => (
                                <option key={opt} value={opt} className="text-gray-900">{opt}</option>
                              ))}
                            </select>
                          </div>
                        );
                      }
                      return (
                        <div key={field.name}>
                          <label htmlFor={field.name} className="block text-xs font-medium text-white/70 mb-1">
                            {field.label} {field.required && <span className="text-accent-yellow">*</span>}
                          </label>
                          <input
                            id={field.name}
                            type={field.type}
                            name={field.name}
                            required={field.required}
                            value={formData[field.name] ?? ""}
                            onChange={handleChange}
                            className={inputClass}
                          />
                        </div>
                      );
                    })}
                  </div>

                  {contactForm.fields.filter(f => f.type === "textarea").map((field) => (
                    <div key={field.name}>
                      <label htmlFor={field.name} className="block text-xs font-medium text-white/70 mb-1">
                        {field.label} {field.required && <span className="text-accent-yellow">*</span>}
                      </label>
                      <textarea
                        id={field.name}
                        name={field.name}
                        required={field.required}
                        rows={5}
                        value={formData[field.name] ?? ""}
                        onChange={handleChange}
                        className={inputClass}
                      />
                    </div>
                  ))}

                  {/* Send button right-aligned */}
                  {/* A failure has to be VISIBLE. Without this the only
                      feedback was the success screen, which the old handler
                      showed unconditionally. */}
                  {error && (
                    <p role="alert" className="text-accent-yellow text-sm bg-white/10 rounded-md px-4 py-3">
                      {error}
                    </p>
                  )}

                  <div className="flex justify-end pt-1">
                    <button
                      type="submit"
                      disabled={sending || cooldown > 0}
                      className="bg-linear-to-r from-accent-green to-accent-green-dark hover:-translate-y-0.5 hover:shadow-lg hover:shadow-accent-green/30 text-primary-darker font-heading font-bold py-3 px-10 rounded-md transition-all duration-200 text-base disabled:opacity-60 disabled:cursor-not-allowed disabled:hover:translate-y-0 disabled:hover:shadow-none"
                    >
                      {sending
                        ? "Enviando..."
                        : cooldown > 0
                          ? `${contactForm.cooldownLabel} ${cooldown}s`
                          : contactForm.submitLabel}
                    </button>
                  </div>

                  {/* Bottom row: aviso legal + social */}
                  <div className="flex items-center justify-between pt-2 border-t border-white/20">
                    <Link to="/aviso-legal" className="text-white/60 hover:text-white text-sm underline transition-colors">
                      {contactForm.legalLinkLabel}
                    </Link>
                    <div className="flex items-center gap-3">
                      {/* Instagram — brand gradient tile */}
                      <a href={siteConfig.social.instagram} target="_blank" rel="noopener noreferrer" aria-label="Instagram" className="hover:scale-110 hover:opacity-90 transition-all duration-200">
                        <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-instagram">
                          <svg className="w-5 h-5 text-white" fill="currentColor" viewBox="0 0 24 24">
                            <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/>
                          </svg>
                        </div>
                      </a>
                      {/* Facebook — brand blue tile */}
                      <a href={siteConfig.social.facebook} target="_blank" rel="noopener noreferrer" aria-label="Facebook" className="hover:scale-110 hover:opacity-90 transition-all duration-200">
                        <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-facebook">
                          <svg className="w-5 h-5 text-white" fill="currentColor" viewBox="0 0 24 24">
                            <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>
                          </svg>
                        </div>
                      </a>
                    </div>
                  </div>
                </form>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Rendered LAST and fixed-positioned, so it is not clipped by the
          contact rectangle's `rounded-2xl` overflow or trapped in its stacking
          context. */}
      <ContactSuccessDialog open={submitted} visible={dialogVisible} onClose={closeDialog} />
    </section>
  );
}
