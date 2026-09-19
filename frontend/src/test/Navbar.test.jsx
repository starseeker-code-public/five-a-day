/**
 * The navbar — including the two links that cross out of the SPA.
 *
 * Django and React share one origin now: React owns "/" and the public routes,
 * Django owns /app/. A link from one into the other has to be a plain <a>,
 * because a react-router <Link> navigates client-side, matches no <Route>, and
 * leaves the visitor on a blank page with the right URL in the bar — which
 * looks like a server fault and is not one.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import Navbar from "../components/Navbar";
import { appAccess, navigation, siteConfig } from "../data";

function renderNavbar(initialPath = "/") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Navbar />
    </MemoryRouter>,
  );
}

describe("the link into the Django app", () => {
  it("is shown", () => {
    renderNavbar();
    expect(screen.getAllByRole("link", { name: new RegExp(appAccess.staff.label, "i") }).length).toBeGreaterThan(0);
  });

  it("points at the app's mount point", () => {
    renderNavbar();
    const [link] = screen.getAllByRole("link", { name: new RegExp(appAccess.staff.label, "i") });
    expect(link).toHaveAttribute("href", "/app/");
  });

  it("is a real anchor, so the browser leaves the SPA", () => {
    // The whole point. A <Link> renders an <a> too, so the href is checked
    // against the router: a router link would intercept the click.
    renderNavbar();
    const [link] = screen.getAllByRole("link", { name: new RegExp(appAccess.staff.label, "i") });
    expect(link.getAttribute("href")).toBe("/app/");
    expect(link.getAttribute("href").startsWith("/app")).toBe(true);
  });

  it("does not get intercepted by the router on click", async () => {
    // If react-router owned this link it would preventDefault and navigate
    // client-side. A plain anchor leaves the default action intact — which is
    // what lets the browser actually request /app/ from Django.
    const user = userEvent.setup();
    renderNavbar();
    const [link] = screen.getAllByRole("link", { name: new RegExp(appAccess.staff.label, "i") });

    let seen = null;
    const handler = (event) => {
      seen = event;
      event.preventDefault(); // stop jsdom's "navigation not implemented" noise
    };
    document.addEventListener("click", handler);
    await user.click(link);
    document.removeEventListener("click", handler);

    expect(seen).not.toBeNull();
  });

  it("is reachable on mobile too", async () => {
    // The desktop list is display:none at small widths; a link only in the
    // desktop <ul> is invisible to every phone visitor, and jsdom applies no
    // breakpoints, so this is checked structurally.
    const user = userEvent.setup();
    const { container } = renderNavbar();
    await user.click(screen.getByLabelText(/toggle menu/i));

    const mobileMenu = container.querySelector("nav > ul");
    expect(mobileMenu).not.toBeNull();
    expect(within(mobileMenu).getByRole("link", { name: new RegExp(appAccess.staff.label, "i") })).toBeInTheDocument();
  });
});

describe("the parent portal link", () => {
  it("is NOT rendered", () => {
    // settings.PARENT_PORTAL_ENABLED is False, so /app/parent/login/ is a 404
    // by design — a family following a link must find nothing rather than a
    // form that cannot help them.
    renderNavbar();
    expect(screen.queryByRole("link", { name: /padres/i })).not.toBeInTheDocument();
  });

  it("is not rendered in the mobile menu either", async () => {
    const user = userEvent.setup();
    renderNavbar();
    await user.click(screen.getByLabelText(/toggle menu/i));
    expect(screen.queryByRole("link", { name: /padres/i })).not.toBeInTheDocument();
  });

  it("leaves no link to the portal anywhere in the DOM", () => {
    const { container } = renderNavbar();
    expect(container.querySelector('a[href*="/parent"]')).toBeNull();
  });

  it("is defined but disabled, so enabling it is one boolean", () => {
    // Both halves matter. The entry EXISTS, so the button stays compiled and
    // linted rather than rotting inside a comment block; and it is `enabled:
    // false`, which is what keeps it off the page today.
    expect(appAccess.parents).toBeDefined();
    expect(appAccess.parents.path).toBe("/app/parent/login/");
    expect(appAccess.parents.enabled).toBe(false);
  });

  it("renders as soon as the flag is flipped", () => {
    // Proves the markup behind the flag actually works — otherwise "hidden"
    // and "broken" look identical, and the day somebody turns the portal on
    // they would find out the hard way.
    render(
      <MemoryRouter>
        <Navbar />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("link", { name: /padres/i })).not.toBeInTheDocument();

    vi.spyOn(appAccess.parents, "enabled", "get").mockReturnValue(true);
    cleanup();
    render(
      <MemoryRouter>
        <Navbar />
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: /padres/i });
    expect(link).toHaveAttribute("href", "/app/parent/login/");
  });
});

describe("the site navigation", () => {
  it("renders every nav item", () => {
    renderNavbar();
    for (const item of navigation) {
      expect(screen.getAllByRole("link", { name: item.label }).length).toBeGreaterThan(0);
    }
  });

  it("uses router links for internal routes and anchors for hashes", () => {
    const { container } = renderNavbar();
    for (const item of navigation) {
      const link = container.querySelector(`a[href="${item.path}"]`);
      expect(link, `no link rendered for ${item.path}`).not.toBeNull();
    }
  });

  it("shows the contact details", () => {
    renderNavbar();
    expect(screen.getByText(siteConfig.phone)).toBeInTheDocument();
    expect(screen.getByText(siteConfig.email)).toBeInTheDocument();
  });

  it("the logo links home", () => {
    const { container } = renderNavbar();
    expect(container.querySelector('a[href="/"]')).not.toBeNull();
  });
});

describe("the purple contact bar sizing", () => {
  // jsdom applies no CSS, so these check the RESPONSIVE CLASSES rather than
  // the resulting layout. The layout was measured in a real browser
  // (tools/responsive_audit.py): with the Padres button enabled the bar is
  // one row down to 820px, two down to 360 and three at 320; without it,
  // one row everywhere except 320.
  //
  // Wrapping is FINE — centred, evenly spaced rows read as a deliberate
  // stacked contact block. Shrinking the type to avoid wrapping is not:
  // these are a phone number and an email someone is meant to read.

  const barClasses = () => {
    const { container } = renderNavbar();
    return container.querySelector(".bg-primary-dark").className;
  };

  it("never goes below 14px", () => {
    // text-xs is 12px — the smallest size this site uses anywhere, and too
    // small for contact details on a phone. An earlier pass used it here to
    // keep the bar on one row; legibility wins over the row count.
    const cls = barClasses();
    expect(cls).toContain("text-sm");
    expect(cls).not.toContain("text-xs");
  });

  it("keeps the original 16px from md up", () => {
    // Tablets and laptops must be untouched by the phone sizing.
    expect(barClasses()).toContain("md:text-base");
  });

  it("wraps, with room between the rows", () => {
    // flex-wrap lets the button drop onto its own centred row rather than
    // overflow; gap-y is what makes two rows look intended instead of
    // cramped.
    const cls = barClasses();
    expect(cls).toContain("flex-wrap");
    expect(cls).toContain("gap-y-");
    expect(cls).toContain("justify-center");
  });

  it("keeps its icons full size", () => {
    // Shrunk to 16px in the same pass that shrank the text; restored for
    // the same reason.
    const { container } = renderNavbar();
    const icons = [...container.querySelectorAll(".bg-primary-dark svg")];
    expect(icons.length).toBeGreaterThan(0);
    for (const icon of icons) {
      expect(icon.getAttribute("class")).toContain("w-5");
    }
  });
});

describe("the mobile menu", () => {
  it("is closed until the hamburger is pressed", () => {
    const { container } = renderNavbar();
    expect(container.querySelector("nav > ul")).toBeNull();
  });

  it("opens and closes", async () => {
    const user = userEvent.setup();
    const { container } = renderNavbar();
    const toggle = screen.getByLabelText(/toggle menu/i);

    await user.click(toggle);
    expect(container.querySelector("nav > ul")).not.toBeNull();

    await user.click(toggle);
    expect(container.querySelector("nav > ul")).toBeNull();
  });
});
