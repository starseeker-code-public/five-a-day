import { useEffect } from "react";

/* ═══════════════════════════════════════════════════════════════
   usePreloadImages – loads every site image quietly in the
   background so pages never flicker when navigating.

   IMPORTANT: the download is deliberately delayed until the browser
   is idle. Downloading all 31 images the instant the page opens made
   them compete with the main hero image for bandwidth, which slowed
   down the visible part of the page — and Google measures exactly
   that (Largest Contentful Paint) as a ranking signal.

   Waiting for idle keeps the no-flicker behaviour while letting the
   first screen render at full speed.
   ═══════════════════════════════════════════════════════════════ */
export function usePreloadImages(urls) {
  useEffect(() => {
    let cancelled = false;

    const preload = () => {
      if (cancelled) return;
      urls.forEach((src) => {
        const img = new Image();
        img.decoding = "async";
        img.src = src;
      });
    };

    /* requestIdleCallback isn't available in Safari, so fall back
       to a short timer after the page has finished loading. */
    const schedule = () =>
      "requestIdleCallback" in window
        ? window.requestIdleCallback(preload, { timeout: 3000 })
        : window.setTimeout(preload, 1500);

    let handle;
    if (document.readyState === "complete") {
      handle = schedule();
    } else {
      window.addEventListener("load", () => {
        handle = schedule();
      }, { once: true });
    }

    return () => {
      cancelled = true;
      if (handle && "cancelIdleCallback" in window) {
        window.cancelIdleCallback(handle);
      }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
