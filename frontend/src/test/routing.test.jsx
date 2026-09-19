/**
 * Every public route renders its page.
 *
 * This is the client-side half of "everything returns 200": Django answering
 * 200 for /faq only proves it served the SHELL — the same shell it serves for
 * every route. Whether React then renders a page or throws is invisible from
 * the server, and a component that throws on mount produces a blank white page
 * with a 200 status. These tests mount the real router at each path and assert
 * something actually rendered.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Layout from "../components/Layout";
import ScrollToTop from "../components/ScrollToTop";
import AvisoLegal from "../pages/AvisoLegal";
import Home from "../pages/Home";
import LosODS from "../pages/LosODS";
import NuestraMetodologia from "../pages/NuestraMetodologia";
import PreguntasFrecuentes from "../pages/PreguntasFrecuentes";
import QuienesSomos from "../pages/QuienesSomos";
import SobreLaAcademia from "../pages/SobreLaAcademia";

/**
 * The route table, mirroring App.jsx. App.jsx itself mounts a BrowserRouter,
 * which cannot be pointed at an arbitrary path from a test, so the same routes
 * are mounted here under MemoryRouter. `project/tests/integration/
 * test_frontend_site.py` is what pins this list against App.jsx and against
 * Django's SPA_ROUTES — all three have to agree.
 */
const ROUTES = [
  { path: "/", element: <Home />, name: "Home" },
  { path: "/quienes-somos", element: <QuienesSomos />, name: "QuienesSomos" },
  { path: "/nuestra-metodologia", element: <NuestraMetodologia />, name: "NuestraMetodologia" },
  { path: "/ods", element: <LosODS />, name: "LosODS" },
  { path: "/sobre-la-academia", element: <SobreLaAcademia />, name: "SobreLaAcademia" },
  { path: "/faq", element: <PreguntasFrecuentes />, name: "PreguntasFrecuentes" },
  { path: "/aviso-legal", element: <AvisoLegal />, name: "AvisoLegal" },
];

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ScrollToTop />
      <Routes>
        <Route element={<Layout />}>
          {ROUTES.map(({ path: p, element }) =>
            p === "/" ? (
              <Route key={p} index element={element} />
            ) : (
              <Route key={p} path={p.slice(1)} element={element} />
            ),
          )}
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("every public route renders", () => {
  let errorSpy;

  beforeEach(() => {
    // React logs render errors through console.error rather than rethrowing in
    // some paths, so a page could "render" while reporting a broken hook or a
    // missing prop. Failing on that is the point: it is exactly the class of
    // problem a 200 from Django cannot see.
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  it.each(ROUTES)("$name renders at $path without errors", ({ path }) => {
    expect(() => renderAt(path)).not.toThrow();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it.each(ROUTES)("$name renders visible content at $path", ({ path }) => {
    const { container } = renderAt(path);
    // A page that mounted but rendered nothing is the failure mode a status
    // code hides, so assert on real text length rather than on the DOM
    // existing.
    expect(container.textContent.trim().length).toBeGreaterThan(200);
  });

  it.each(ROUTES)("$name renders exactly one <h1> at $path", ({ path }) => {
    // One h1 per page: the pages are the site's SEO surface, and both zero and
    // several are defects a human would have to notice by eye.
    const { container } = renderAt(path);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
  });

  it.each(ROUTES)("$name renders the shared chrome at $path", ({ path }) => {
    renderAt(path);
    expect(screen.getByRole("navigation")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
  });
});

describe("images", () => {
  it.each(ROUTES)("every <img> on $name has alt text", ({ path }) => {
    // Accessibility, and it catches a half-written <img> too.
    const { container } = renderAt(path);
    const missing = [...container.querySelectorAll("img")]
      .filter((img) => !img.getAttribute("alt"))
      .map((img) => img.getAttribute("src"));
    expect(missing).toEqual([]);
  });

  it.each(ROUTES)("every <img> on $name uses a root-absolute src", ({ path }) => {
    // Django + WhiteNoise serve these from the origin root. A relative src
    // ("images/x.png") resolves against the CURRENT route, so it would load on
    // "/" and 404 on "/quienes-somos" — a bug that only shows on sub-pages.
    const { container } = renderAt(path);
    const relative = [...container.querySelectorAll("img")]
      .map((img) => img.getAttribute("src"))
      .filter((src) => src && !src.startsWith("/") && !src.startsWith("data:") && !src.startsWith("http"));
    expect(relative).toEqual([]);
  });
});

describe("the aviso legal page", () => {
  it("is reachable from the contact block's legal link", () => {
    renderAt("/");
    const footer = screen.getByRole("contentinfo");
    // Rendered somewhere on the home page, so a visitor can always reach the
    // legal notice — it is a legal requirement, not a nicety.
    const links = [...document.querySelectorAll('a[href="/aviso-legal"]')];
    expect(links.length).toBeGreaterThan(0);
    expect(footer).toBeInTheDocument();
  });
});

describe("outbound links", () => {
  it.each(ROUTES)("external links on $name are safe", ({ path }) => {
    // target=_blank without rel=noopener hands the opened tab a reference back
    // to window.opener. Checked here rather than reviewed by eye because the
    // site links out to WhatsApp, Instagram, Facebook, Google Maps and the UN.
    const { container } = renderAt(path);
    const unsafe = [...container.querySelectorAll('a[target="_blank"]')]
      .filter((a) => !(a.getAttribute("rel") || "").includes("noopener"))
      .map((a) => a.getAttribute("href"));
    expect(unsafe).toEqual([]);
  });
});

describe("the route table", () => {
  it("mirrors App.jsx", async () => {
    // Guards the mirror this file is built on: a route added to App.jsx but not
    // here would silently go untested, which is worse than untested-and-known.
    const source = await import("../App.jsx?raw").then((m) => m.default);
    const declared = [...source.matchAll(/<Route\s+path="([^"]+)"/g)].map((m) => `/${m[1]}`);
    const covered = ROUTES.map((r) => r.path).filter((p) => p !== "/");
    expect(new Set(declared)).toEqual(new Set(covered));
    expect(source).toContain("<Route index");
  });
});
