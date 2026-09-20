/**
 * Integrity of the site's content file.
 *
 * `data.js` is where all of the site's copy, links and image paths live, and
 * nothing type-checks it. These are the mistakes it can hold that no page test
 * would catch, because a broken link still renders and a missing image still
 * produces an <img> tag:
 *
 *   - a nav entry pointing at a route that does not exist (this really
 *     happened: "Saber más" pointed at /los-ods while the route is /ods, so the
 *     link 404s under Django and rendered a blank page on Netlify);
 *   - an image path with no file behind it;
 *   - a contact field the Django endpoint does not know about, which it drops.
 */
import { existsSync } from "node:fs";
import { readdir, readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import * as data from "../data";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, "..", "..");
const PUBLIC_DIR = join(FRONTEND, "public");

/** Every "/…" string anywhere in data.js, however deeply nested. */
function collectPaths(value, found = new Set()) {
  if (typeof value === "string") {
    if (value.startsWith("/")) found.add(value);
  } else if (Array.isArray(value)) {
    value.forEach((v) => collectPaths(v, found));
  } else if (value && typeof value === "object") {
    Object.values(value).forEach((v) => collectPaths(v, found));
  }
  return found;
}

const ALL_PATHS = [...collectPaths(data)];
const ASSET_PATHS = ALL_PATHS.filter((p) => /\.(png|jpe?g|svg|gif|webp|mp4|webm|ico)$/i.test(p));
const ROUTE_PATHS = ALL_PATHS.filter((p) => !ASSET_PATHS.includes(p));

async function declaredRoutes() {
  const source = await readFile(join(FRONTEND, "src", "App.jsx"), "utf8");
  const routes = [...source.matchAll(/<Route\s+path="([^"]+)"/g)].map((m) => `/${m[1]}`);
  return new Set(["/", ...routes]);
}

describe("asset paths", () => {
  it("finds some to check", () => {
    // A guard on the guard: if data.js is restructured so nothing is collected,
    // every test below would pass vacuously.
    expect(ASSET_PATHS.length).toBeGreaterThan(10);
  });

  it.each(ASSET_PATHS)("%s exists in public/", (assetPath) => {
    expect(existsSync(join(PUBLIC_DIR, assetPath))).toBe(true);
  });
});

describe("internal links", () => {
  it("every non-hash path in data.js is a real route", async () => {
    const routes = await declaredRoutes();
    const broken = ROUTE_PATHS.filter((p) => !p.startsWith("/#") && !routes.has(p) && !p.startsWith("/app"));
    expect(broken).toEqual([]);
  });

  it("every navigation entry resolves", async () => {
    const routes = await declaredRoutes();
    const broken = data.navigation
      .map((item) => item.path)
      .filter((p) => !p.startsWith("#") && !routes.has(p));
    expect(broken).toEqual([]);
  });
});

describe("links into the Django app", () => {
  it("the staff link targets the app prefix", () => {
    expect(data.appAccess.staff.path).toBe("/app/");
  });

  it("the parent portal entry is present but disabled", () => {
    // Kept as live code behind a flag rather than commented out, so it cannot
    // rot while the portal is off. `enabled` is what hides it.
    expect(data.appAccess.parents.enabled).toBe(false);
    expect(data.appAccess.parents.path).toBe("/app/parent/login/");
  });
});

describe("the contact form definition", () => {
  it("declares the fields the Django endpoint expects", () => {
    // Mirrored by CONTACT_FIELDS in core/views/frontend.py. A field declared on
    // one side only is collected by the form and never shown in the email.
    const names = data.contactForm.fields.map((f) => f.name);
    expect(new Set(names)).toEqual(
      new Set(["nombre", "apellidos", "email", "telefono", "horario", "edad", "mensaje"]),
    );
  });

  it("marks the fields the server treats as required", () => {
    const required = data.contactForm.fields.filter((f) => f.required).map((f) => f.name);
    expect(new Set(required)).toEqual(new Set(["nombre", "email", "telefono", "mensaje"]));
  });

  it("gives every field a label", () => {
    // The label is both the visible text and the accessible name.
    for (const field of data.contactForm.fields) {
      expect(field.label, `field ${field.name} has no label`).toBeTruthy();
    }
  });

  it("gives the select some options", () => {
    for (const field of data.contactForm.fields.filter((f) => f.type === "select")) {
      expect(field.options?.length, `select ${field.name} has no options`).toBeGreaterThan(0);
    }
  });
});

describe("external links", () => {
  it("are all https", async () => {
    const source = await readFile(join(FRONTEND, "src", "data.js"), "utf8");
    const insecure = [...source.matchAll(/["'](http:\/\/[^"']+)["']/g)].map((m) => m[1]);
    expect(insecure).toEqual([]);
  });

  it("the whatsapp and email details are set", () => {
    expect(data.siteConfig.whatsapp).toMatch(/^https:\/\/wa\.me\//);
    expect(data.siteConfig.email).toMatch(/@/);
    expect(data.siteConfig.phone).toBeTruthy();
  });
});

describe("public/ has nothing unreferenced that is large", () => {
  it("every video in public/ is used", async () => {
    // Videos dominate the image and the repo (~15 MB here). An orphaned one is
    // pure weight in every clone and every Cloud Run cold start.
    const videos = await readdir(join(PUBLIC_DIR, "videos"));
    const referenced = new Set(ASSET_PATHS.map((p) => p.split("/").pop()));
    const orphans = videos.filter((file) => !referenced.has(file));
    expect(orphans).toEqual([]);
  });
});
