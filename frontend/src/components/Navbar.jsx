import { useState, useEffect } from "react";
import { Link, NavLink } from "react-router-dom";
import { siteConfig, navigation, appAccess } from "../data";

export default function Navbar() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <>
      {/* Top contact bar — dark purple, larger font */}
      {/* Three items live here: phone, email and the Padres button, and the
          email alone is the widest thing in the bar.

          LEGIBILITY OVER COMPACTNESS. Wrapping to a second or third row on a
          small phone is fine — these are centred, evenly spaced rows and they
          read as a deliberate stacked contact block. Shrinking the type to fit
          is not fine: an earlier pass took it to 12px, which is the smallest
          size this site uses anywhere and too small for a phone number someone
          is meant to read and dial. 14px is the floor; md and up keeps the
          original 16px, so tablets and laptops are untouched.

          gap-y is deliberately generous for the same reason — two tight rows
          look like a mistake, two spaced rows look intended. */}
      <div className="bg-primary-dark text-white text-sm md:text-base py-2 md:py-2.5 px-3 sm:px-4 flex flex-wrap items-center justify-center gap-x-4 gap-y-2 md:gap-10">
        <a
          href={siteConfig.whatsapp}
          className="flex items-center gap-2 hover:text-accent-green transition-colors"
        >
          <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z" />
          </svg>
          {siteConfig.phone}
        </a>
        <a href={`mailto:${siteConfig.email}`} className="flex items-center gap-2 hover:text-accent-green transition-colors">
          <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
          {siteConfig.email}
        </a>

        {/* PARENT PORTAL. Hidden by `appAccess.parents.enabled` (see the note
            beside it in ../data.js) rather than commented out, so it stays
            compiled, linted and rendered by the tests while it waits.

            A conditional render, NOT a `hidden` class: a CSS-hidden link is
            still in the DOM, so a crawler indexes it and anyone reading the
            page source finds a live-looking route that answers 404.

            It belongs in THIS bar rather than the white nav below because that
            is where a family looks for how to reach the academy, and the bar
            renders at every width — so there is no mobile copy to maintain.
            Outlined like the App button, inverted for the dark background: a
            solid fill would read as the page's primary action, which it is not.

            A plain <a>, NOT <NavLink>: /app/ is Django's, so a router link
            would navigate client-side, match no <Route> and blank the page. */}
        {appAccess.parents.enabled && (
          <a
            href={appAccess.parents.path}
            title={appAccess.parents.title}
            className="inline-flex items-center gap-2 rounded-md border-2 border-white/50 px-3 py-1 font-heading text-sm font-semibold uppercase tracking-wide hover:bg-white hover:text-primary-dark hover:border-white transition-colors"
          >
            <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
            </svg>
            {appAccess.parents.label}
          </a>
        )}
      </div>

      {/* Main nav — white, sticky */}
      <nav className={`bg-white/95 backdrop-blur-sm sticky top-0 z-50 transition-shadow duration-300 ${scrolled ? "shadow-lg" : "shadow-sm"}`}>
        {/* h-32 at every width put a 128px sticky bar on a 568px phone. The
            logo is what drives the height, so both step down together. */}
        <div className="max-w-[980px] mx-auto px-4 flex items-center justify-between h-20 sm:h-24 lg:h-32">
          <Link to="/" className="flex items-center shrink-0">
            <img src="/images/logo_transparent.png" alt="Five a Day English Academy" className="h-16 sm:h-20 lg:h-28 w-auto" />
          </Link>

          {/* Desktop links */}
          <ul className="hidden lg:flex items-center gap-4">
            {navigation.map((item) => (
              <li key={item.path}>
                {item.path.startsWith("#") ? (
                  <a
                    href={item.path}
                    className="font-heading text-xs font-semibold text-primary-dark hover:text-primary transition-colors uppercase tracking-wide"
                  >
                    {item.label}
                  </a>
                ) : (
                  <NavLink
                    to={item.path}
                    className={({ isActive }) =>
                      `font-heading text-xs font-semibold uppercase tracking-wide transition-colors ${
                        isActive
                          ? "text-primary border-b-2 border-primary pb-1"
                          : "text-primary-dark hover:text-primary"
                      }`
                    }
                  >
                    {item.label}
                  </NavLink>
                )}
              </li>
            ))}

            {/* Into the Django app. A plain <a>, NOT <NavLink>: /app/ is served
                by Django, so a router link would navigate client-side, match no
                <Route>, and render a blank page. (The parent portal link lives
                in the purple bar above, not here.) */}
            <li className="ml-2">
              <a
                href={appAccess.staff.path}
                title={appAccess.staff.title}
                className="inline-flex items-center gap-1.5 font-heading text-xs font-semibold uppercase tracking-wide rounded-md border-2 border-primary text-primary px-4 py-2 hover:bg-primary hover:text-white transition-colors"
              >
                <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 16l-4-4m0 0l4-4m-4 4h14m-5 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h7a3 3 0 013 3v1" />
                </svg>
                {appAccess.staff.label}
              </a>
            </li>
          </ul>

          {/* Hamburger */}
          {/* p-2 -m-2 grows the hit area from the icon's 28x28 to 44x44 — the
              minimum for a finger — without shifting where it appears. It is
              the ONLY navigation control on a phone or tablet, so it was the
              most-used undersized target on the site. */}
          <button
            onClick={() => setOpen(!open)}
            className="lg:hidden text-primary-dark focus:outline-none p-2 -m-2"
            aria-label="Toggle menu"
            aria-expanded={open}
            type="button"
          >
            <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              {open ? (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              ) : (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              )}
            </svg>
          </button>
        </div>

        {/* Mobile menu */}
        {open && (
          <ul className="lg:hidden bg-white border-t border-primary/10 px-4 pb-4 space-y-1">
            {navigation.map((item) => (
              <li key={item.path}>
                {item.path.startsWith("#") ? (
                  <a
                    href={item.path}
                    onClick={() => setOpen(false)}
                    className="block py-3 font-heading text-sm font-semibold text-primary-dark hover:text-primary uppercase tracking-wide"
                  >
                    {item.label}
                  </a>
                ) : (
                  <NavLink
                    to={item.path}
                    onClick={() => setOpen(false)}
                    className="block py-3 font-heading text-sm font-semibold text-primary-dark hover:text-primary uppercase tracking-wide"
                  >
                    {item.label}
                  </NavLink>
                )}
              </li>
            ))}

            {/* Same <a>-not-<Link> rule as the desktop button above. */}
            <li className="pt-2 border-t border-primary/10">
              <a
                href={appAccess.staff.path}
                title={appAccess.staff.title}
                onClick={() => setOpen(false)}
                className="flex items-center justify-center gap-2 mt-2 font-heading text-sm font-semibold uppercase tracking-wide rounded-md border-2 border-primary text-primary px-4 py-3 hover:bg-primary hover:text-white transition-colors"
              >
                <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 16l-4-4m0 0l4-4m-4 4h14m-5 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h7a3 3 0 013 3v1" />
                </svg>
                {appAccess.staff.label}
              </a>
            </li>

          </ul>
        )}
      </nav>
    </>
  );
}
