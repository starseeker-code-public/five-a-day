/* ═══════════════════════════════════════════════════════════════
   generate-seo.mjs – runs automatically after every build
   ═══════════════════════════════════════════════════════════════

   THE PROBLEM THIS SOLVES
   Our website is a "single page application": every address on the
   site (/faq, /quienes-somos, ...) was being served the exact same
   HTML file, with the exact same title. Google therefore saw seven
   pages that all looked identical, and could not rank any of them
   for their own topic.

   WHAT THIS SCRIPT DOES
   After Vite builds the site into the `dist` folder, this script
   creates one real HTML file per page — dist/faq/index.html and so
   on — each with its own title, description, canonical address,
   sharing image and Google structured data.

   It also writes:
   - dist/sitemap.xml  the list of pages we want Google to index
   - dist/robots.txt   the instructions for search engine crawlers

   WHO SERVES THESE FILES
   Django, not Netlify. `core/views/frontend.py::frontend_index` looks
   for dist/<route>/index.html first and falls back to dist/index.html.
   That fallback matters: without a build, and in the test suite, the
   per-page files do not exist and the site must still work.

   This is the part to re-check if the site ever moves again. On Netlify
   the per-page files were picked up by the static-file server for free;
   under Django a view has to go looking for them. Generating them
   without teaching the server to serve them is a SILENT failure — the
   build succeeds, the files sit in dist/, and every route keeps serving
   the homepage's title to Google.

   Nothing here needs editing to add a page — add it to src/seo.js
   and it will appear automatically. The route must also exist in
   `SPA_ROUTES` in core/views/frontend.py, which a test enforces.
   ═══════════════════════════════════════════════════════════════ */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_OG_IMAGE,
  SITE_URL,
  absoluteUrl,
  pagesSeo,
  schemasForPage,
} from "../src/seo.js";
import { siteConfig } from "../src/data.js";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DIST = join(ROOT, "dist");

const START = "<!-- SEO:START -->";
const END = "<!-- SEO:END -->";

/* Escapes the characters that would otherwise break an HTML attribute. */
const escapeAttr = (value) =>
  String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

/* Escapes "</script" so a JSON-LD block can never close its own tag. */
const escapeJsonLd = (data) =>
  JSON.stringify(data).replace(/</g, "\\u003c");

/* Builds the full block of SEO tags for one page. */
function buildSeoTags(page) {
  const url = absoluteUrl(page.path);
  const title = escapeAttr(page.title);
  const description = escapeAttr(page.description);

  const tags = [
    `<title>${title}</title>`,
    `<meta name="description" content="${description}" />`,
    `<link rel="canonical" href="${url}" />`,
    page.noindex
      ? `<meta name="robots" content="noindex, follow" />`
      : `<meta name="robots" content="index, follow, max-image-preview:large" />`,

    `<meta property="og:type" content="website" />`,
    `<meta property="og:url" content="${url}" />`,
    `<meta property="og:title" content="${title}" />`,
    `<meta property="og:description" content="${description}" />`,
    `<meta property="og:image" content="${DEFAULT_OG_IMAGE}" />`,
    `<meta property="og:image:width" content="1200" />`,
    `<meta property="og:image:height" content="630" />`,
    `<meta property="og:locale" content="es_ES" />`,
    `<meta property="og:site_name" content="${escapeAttr(siteConfig.fullName)}" />`,

    `<meta name="twitter:card" content="summary_large_image" />`,
    `<meta name="twitter:title" content="${title}" />`,
    `<meta name="twitter:description" content="${description}" />`,
    `<meta name="twitter:image" content="${DEFAULT_OG_IMAGE}" />`,

    `<meta name="geo.region" content="ES-AB" />`,
    `<meta name="geo.placename" content="${escapeAttr(siteConfig.address.city)}" />`,
    `<meta name="geo.position" content="${siteConfig.geo.latitude};${siteConfig.geo.longitude}" />`,
    `<meta name="ICBM" content="${siteConfig.geo.latitude}, ${siteConfig.geo.longitude}" />`,
  ];

  for (const schema of schemasForPage(page)) {
    tags.push(
      `<script type="application/ld+json">${escapeJsonLd(schema)}</script>`,
    );
  }

  return tags.map((tag) => `    ${tag}`).join("\n");
}

/* Writes dist/<path>/index.html (or dist/index.html for the homepage). */
async function writePage(template, page) {
  const from = template.indexOf(START);
  const to = template.indexOf(END);
  if (from === -1 || to === -1) {
    throw new Error(
      "Could not find the SEO:START / SEO:END markers in dist/index.html. " +
        "They must stay in index.html for per-page SEO to work.",
    );
  }

  const html =
    template.slice(0, from + START.length) +
    "\n" +
    buildSeoTags(page) +
    "\n" +
    template.slice(to);

  const outDir = page.path === "/" ? DIST : join(DIST, page.path);
  await mkdir(outDir, { recursive: true });
  await writeFile(join(outDir, "index.html"), html, "utf8");
  return join(outDir, "index.html");
}

async function writeSitemap() {
  const today = new Date().toISOString().slice(0, 10);
  const urls = pagesSeo
    .filter((page) => !page.noindex)
    .map(
      (page) =>
        `  <url>\n` +
        `    <loc>${absoluteUrl(page.path)}</loc>\n` +
        `    <lastmod>${today}</lastmod>\n` +
        `    <changefreq>monthly</changefreq>\n` +
        `    <priority>${page.priority}</priority>\n` +
        `  </url>`,
    )
    .join("\n");

  const xml =
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    `${urls}\n` +
    `</urlset>\n`;

  await writeFile(join(DIST, "sitemap.xml"), xml, "utf8");
}

async function writeRobots() {
  /* `/app/` is the academy's private management application, sharing this
     origin with the public site. It has no business in a search index: every
     page behind it redirects to a staff login, so crawling it yields nothing
     but login pages, and the URLs leak the shape of the back office.

     Note: pages we want kept out of Google (the legal notice) are NOT
     blocked here on purpose. Blocking a page in robots.txt stops Google
     from reading it at all — including the "noindex" instruction inside
     it. Letting it be crawled is what actually keeps it out of results. */
  const txt =
    `User-agent: *\n` +
    `Allow: /\n` +
    `Disallow: /app/\n` +
    `\n` +
    `Sitemap: ${SITE_URL}/sitemap.xml\n`;

  await writeFile(join(DIST, "robots.txt"), txt, "utf8");
}

const template = await readFile(join(DIST, "index.html"), "utf8");

/* The homepage is written last so it overwrites the template file
   only after every other page has read from it. */
const ordered = [...pagesSeo].sort((a, b) =>
  a.path === "/" ? 1 : b.path === "/" ? -1 : 0,
);

for (const page of ordered) {
  const file = await writePage(template, page);
  console.log(`  seo  ${file.replace(ROOT, ".")}`);
}

await writeSitemap();
await writeRobots();

console.log(
  `  seo  ${pagesSeo.length} pages + sitemap.xml + robots.txt generated for ${SITE_URL}`,
);
