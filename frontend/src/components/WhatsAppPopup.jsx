import { useState, useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { siteConfig, whatsappPopup } from "../data";

export default function WhatsAppPopup() {
  const [visible, setVisible] = useState(false);
  const [show, setShow] = useState(false);
  const timerRef = useRef(null);
  const autoHideRef = useRef(null);
  const triggeredRef = useRef(false);
  const location = useLocation();

  const dismiss = () => {
    setShow(false);
    setTimeout(() => setVisible(false), 350);
  };

  useEffect(() => {
    clearTimeout(timerRef.current);
    if (triggeredRef.current) return;

    // Function to show the popup with fade-in animation and auto-hide after 10s.
    // Uses triggeredRef to ensure we only show it once per page visit (not on every re-render).
    const trigger = () => {
      if (triggeredRef.current) return;
      triggeredRef.current = true;
      setVisible(true);
      // Small delay before showing to allow CSS animation to trigger
      setTimeout(() => setShow(true), 20);
      // Auto-hide after 10 seconds
      autoHideRef.current = setTimeout(() => {
        setShow(false);
        // Wait for fade-out animation (350ms) before unmounting
        setTimeout(() => setVisible(false), 350);
      }, 10000);
    };

    if (location.pathname === "/") {
      // On home page: trigger 3 seconds after user scrolls past the yellow banner (yellow-banner id).
      // This ensures they see the main content first before we show the popup.
      let handled = false;
      const onScroll = () => {
        if (handled) return;
        const el = document.getElementById("yellow-banner");
        // Check if banner has scrolled off top of screen (getBoundingClientRect().bottom < 0)
        if (!el || el.getBoundingClientRect().bottom >= 0) return;
        handled = true;
        window.removeEventListener("scroll", onScroll);
        timerRef.current = setTimeout(trigger, 3000);
      };
      window.addEventListener("scroll", onScroll, { passive: true });
      return () => {
        window.removeEventListener("scroll", onScroll);
        clearTimeout(timerRef.current);
      };
    } else {
      // On other pages: trigger after 10 seconds from page load
      timerRef.current = setTimeout(trigger, 10000);
      return () => clearTimeout(timerRef.current);
    }
  }, [location.pathname]);

  useEffect(() => () => clearTimeout(autoHideRef.current), []);

  if (!visible) return null;

  return (
    <div className={`fixed bottom-6 right-6 z-50 bg-white rounded-3xl shadow-2xl p-5 flex gap-4 items-start popup-width popup ${show ? "popup-visible" : ""}`}>
      {/* WhatsApp icon */}
      <a href={siteConfig.whatsapp} target="_blank" rel="noopener noreferrer" className="shrink-0">
        <div className="w-11 h-11 bg-whatsapp rounded-full flex items-center justify-center shadow-md">
          <svg className="w-6 h-6 text-white" fill="currentColor" viewBox="0 0 24 24">
            <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
          </svg>
        </div>
      </a>

      {/* Text */}
      <div className="flex-1 min-w-0">
        <p className="font-semibold text-gray-800 text-sm mb-1.5">{whatsappPopup.question}</p>
        <p className="text-gray-500 text-xs leading-relaxed mb-4">{whatsappPopup.body}</p>
        <div className="flex justify-end">
          <a
            href={siteConfig.whatsapp}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block bg-whatsapp hover:bg-whatsapp-dark text-white text-xs font-semibold py-2 px-5 rounded-full transition-colors"
          >
            {whatsappPopup.cta}
          </a>
        </div>
      </div>

      {/* Close */}
      <button
        onClick={dismiss}
        aria-label="Cerrar"
        className="shrink-0 -mt-0.5 text-gray-400 hover:text-gray-700 transition-colors"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}
