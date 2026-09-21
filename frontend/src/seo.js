/* ═══════════════════════════════════════════════════════════════
   seo.js – Single Source of Truth for SEO
   ═══════════════════════════════════════════════════════════════

   This file defines, for every page of the site:
   - The <title> shown in the Google results list
   - The description shown under the title in Google
   - The canonical URL (the one "official" address of the page)
   - The social sharing image (WhatsApp, Facebook, Instagram)

   It also builds the "structured data" (JSON-LD) that tells Google
   we are a real, physical English academy in Albacete — the thing
   that makes us eligible to appear in the local map results.

   USED IN TWO PLACES:
   1. scripts/generate-seo.mjs — at build time, writes a real HTML
      file per page so Google sees the right title immediately.
   2. src/hooks/useSeo.js — at runtime, keeps the browser tab title
      correct while navigating inside the site.

   To change any page title or description, edit this file only.
   ═══════════════════════════════════════════════════════════════ */

import { siteConfig, faqPage, founders } from "./data.js";

/* The live address of the website. If the domain ever changes,
   this is the ONLY line that needs updating. No trailing slash. */
export const SITE_URL = "https://fiveadayenglish.com";

/* Image used when someone shares a link on WhatsApp or Facebook.
   Must be an absolute URL, and ideally 1200x630 pixels. */
export const DEFAULT_OG_IMAGE = `${SITE_URL}/images/og-image.jpg`;

/* ── Per-page SEO ────────────────────────────────────────────────
   `path`        the URL of the page
   `title`       keep under ~60 characters or Google truncates it
   `description` keep under ~155 characters
   `priority`    hint to Google about relative importance (sitemap)
   `noindex`     true = ask Google to keep this page out of results
   ──────────────────────────────────────────────────────────────── */
export const pagesSeo = [
  {
    path: "/",
    title: "Academia de inglés en Albacete | Five a Day English Academy",
    description:
      "Academia de inglés en Albacete para niños, adolescentes y adultos. Método propio basado en la lectoescritura, grupos reducidos y clase de prueba gratuita.",
    priority: "1.0",
  },
  {
    path: "/quienes-somos",
    title: "Quiénes somos | Maestras bilingües de inglés en Albacete",
    description:
      "Conoce a Ms Penélope y Ms Sílvia, las dos maestras bilingües que fundaron Five a Day, con experiencia docente en colegios internacionales de Europa.",
    priority: "0.8",
  },
  {
    path: "/nuestra-metodologia",
    title: "Metodología 5 a Day | Clases de inglés en Albacete",
    description:
      "Descubre el método 5 a Day: cinco rutinas de lectoescritura, sesiones de 1h20, grupos de máximo 8 alumnos y Fun Fridays gratuitos. Único en España.",
    priority: "0.9",
  },
  {
    path: "/ods",
    title: "Los ODS y la educación sostenible | Five a Day Albacete",
    description:
      "Aprender inglés conectado con los Objetivos de Desarrollo Sostenible: educación integral en valores, medioambiente y compromiso con la Agenda 2030.",
    priority: "0.6",
  },
  {
    path: "/sobre-la-academia",
    title: "Sobre la academia | Academia de inglés en Albacete",
    description:
      "Nuestro centro en C/ Hermanos Jiménez 25, Albacete: instalaciones, clases para todas las edades y lo que diferencia a Five a Day de otras academias.",
    priority: "0.8",
  },
  {
    path: "/faq",
    title: "Preguntas frecuentes | Academia de inglés en Albacete",
    description:
      "Horarios, instalaciones, cómo son las clases y cómo inscribirse en Five a Day English Academy en Albacete. Resolvemos tus dudas más habituales.",
    priority: "0.7",
  },
  {
    path: "/aviso-legal",
    title: "Aviso legal | Five a Day English Academy",
    description:
      "Información legal, propiedad intelectual y política de protección de datos de Five a Day English Academy, Albacete.",
    priority: "0.1",
    noindex: true,
  },
];

/* Turns "/quienes-somos" into "https://fiveadayenglish.com/quienes-somos" */
export const absoluteUrl = (path) =>
  path === "/" ? `${SITE_URL}/` : `${SITE_URL}${path}`;

/* Looks up the SEO entry for a URL. Falls back to the homepage. */
export const getPageSeo = (path) =>
  pagesSeo.find((p) => p.path === path) ?? pagesSeo[0];

/* ── Structured data (JSON-LD) ───────────────────────────────────
   These objects are invisible to visitors but are read by Google
   to build rich results and the local map listing.
   ──────────────────────────────────────────────────────────────── */

/* Opening hours, derived from siteConfig.hours.inPerson.
   Monday–Thursday: 11:30–13:30 and 16:00–20:30. Friday: 12:00–18:30. */
const openingHours = [
  {
    "@type": "OpeningHoursSpecification",
    dayOfWeek: ["Monday", "Tuesday", "Wednesday", "Thursday"],
    opens: "11:30",
    closes: "13:30",
  },
  {
    "@type": "OpeningHoursSpecification",
    dayOfWeek: ["Monday", "Tuesday", "Wednesday", "Thursday"],
    opens: "16:00",
    closes: "20:30",
  },
  {
    "@type": "OpeningHoursSpecification",
    dayOfWeek: ["Friday"],
    opens: "12:00",
    closes: "18:30",
  },
];

/* Strips spaces from a Spanish number and adds the country code. */
const toE164 = (number) => `+34${number.replace(/\s/g, "")}`;

/* The main entry: tells Google we are a physical language school
   in Albacete, with this exact name, address, phone and hours. */
export const organizationSchema = {
  "@context": "https://schema.org",
  "@type": ["EducationalOrganization", "LocalBusiness"],
  "@id": `${SITE_URL}/#organization`,
  name: siteConfig.fullName,
  alternateName: siteConfig.name,
  description:
    "Academia de inglés en Albacete para niños, adolescentes y adultos, con una metodología propia basada en la lectoescritura.",
  url: `${SITE_URL}/`,
  logo: `${SITE_URL}/images/logo.png`,
  image: DEFAULT_OG_IMAGE,
  email: siteConfig.email,
  telephone: toE164(siteConfig.landline),
  address: {
    "@type": "PostalAddress",
    streetAddress: siteConfig.address.street,
    postalCode: siteConfig.address.postalCode,
    addressLocality: siteConfig.address.city,
    addressRegion: "Albacete",
    addressCountry: "ES",
  },
  geo: {
    "@type": "GeoCoordinates",
    latitude: siteConfig.geo.latitude,
    longitude: siteConfig.geo.longitude,
  },
  hasMap: siteConfig.mapsUrl,
  openingHoursSpecification: openingHours,
  areaServed: { "@type": "City", name: "Albacete" },
  priceRange: "€€",
  sameAs: [siteConfig.social.instagram, siteConfig.social.facebook],
  contactPoint: [
    {
      "@type": "ContactPoint",
      contactType: "customer service",
      telephone: toE164(siteConfig.landline),
      email: siteConfig.email,
      availableLanguage: ["Spanish", "English"],
    },
    {
      "@type": "ContactPoint",
      contactType: "customer service",
      telephone: toE164(siteConfig.phone),
      availableLanguage: ["Spanish", "English"],
    },
  ],
  founder: founders.map((f) => ({
    "@type": "Person",
    name: f.name.replace(/^Ms\s+/, ""),
    jobTitle: f.role,
  })),
};

/* Lets Google understand the site as a whole and link it to us. */
export const websiteSchema = {
  "@context": "https://schema.org",
  "@type": "WebSite",
  "@id": `${SITE_URL}/#website`,
  url: `${SITE_URL}/`,
  name: siteConfig.fullName,
  inLanguage: "es-ES",
  publisher: { "@id": `${SITE_URL}/#organization` },
};

/* Turns our FAQ page content into the format Google needs to show
   expandable questions directly in the search results. */
export const faqSchema = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: faqPage.questions.map((q) => ({
    "@type": "Question",
    name: q.question,
    acceptedAnswer: {
      "@type": "Answer",
      text: q.answer.replace(/\s*\n+\s*/g, " ").trim(),
    },
  })),
};

/* "Inicio > Nuestra metodología" trail shown under the Google result. */
export const breadcrumbSchema = (page) => ({
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  itemListElement: [
    { "@type": "ListItem", position: 1, name: "Inicio", item: `${SITE_URL}/` },
    ...(page.path === "/"
      ? []
      : [
          {
            "@type": "ListItem",
            position: 2,
            name: page.title.split("|")[0].trim(),
            item: absoluteUrl(page.path),
          },
        ]),
  ],
});

/* Collects every schema that belongs on a given page. */
export const schemasForPage = (page) => {
  const schemas = [breadcrumbSchema(page)];
  if (page.path === "/") schemas.unshift(organizationSchema, websiteSchema);
  if (page.path === "/faq") schemas.push(faqSchema);
  return schemas;
};
