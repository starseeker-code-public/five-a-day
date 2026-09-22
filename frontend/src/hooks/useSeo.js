import { useEffect } from "react";
import { useLocation } from "react-router-dom";

import { absoluteUrl, getPageSeo } from "../seo.js";

/* ═══════════════════════════════════════════════════════════════
   useSeo – keeps the page title correct while browsing the site
   ═══════════════════════════════════════════════════════════════

   When the site is built, every page gets its own real HTML file
   with the right title already inside it (see scripts/generate-seo.mjs).
   That covers a visitor arriving from Google.

   But once someone is *inside* the site and clicks a menu link, the
   page never reloads — React swaps the content instantly. Without
   this hook the browser tab, the bookmark name and the canonical
   address would all stay stuck on whichever page they landed on.

   This hook updates them on every navigation.
   ═══════════════════════════════════════════════════════════════ */

/* Finds a <meta> or <link> tag, creating it if it isn't there yet. */
function upsertTag(selector, create) {
  let el = document.head.querySelector(selector);
  if (!el) {
    el = create();
    document.head.appendChild(el);
  }
  return el;
}

function setMeta(attr, key, content) {
  const el = upsertTag(`${attr === "property" ? "meta[property=" : "meta[name="}"${key}"]`, () => {
    const tag = document.createElement("meta");
    tag.setAttribute(attr, key);
    return tag;
  });
  el.setAttribute("content", content);
}

export function useSeo() {
  const { pathname } = useLocation();

  useEffect(() => {
    const page = getPageSeo(pathname);
    const url = absoluteUrl(page.path);

    document.title = page.title;

    setMeta("name", "description", page.description);
    setMeta("property", "og:title", page.title);
    setMeta("property", "og:description", page.description);
    setMeta("property", "og:url", url);
    setMeta("name", "twitter:title", page.title);
    setMeta("name", "twitter:description", page.description);
    setMeta(
      "name",
      "robots",
      page.noindex ? "noindex, follow" : "index, follow, max-image-preview:large",
    );

    const canonical = upsertTag('link[rel="canonical"]', () => {
      const tag = document.createElement("link");
      tag.setAttribute("rel", "canonical");
      return tag;
    });
    canonical.setAttribute("href", url);
  }, [pathname]);
}
