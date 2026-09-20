/**
 * The contact form — the one part of this site that talks to a server.
 *
 * It used to be a Netlify Form: the markup carried a `netlify` attribute and
 * the handler POSTed to "/", which Netlify intercepted. Django serves the site
 * now, so it posts to /api/contact/ instead.
 *
 * The regression worth remembering is not the endpoint change. The old handler
 * set `submitted = true` inside its `try`, without looking at the response — so
 * a form posting into a void showed the family "¡Mensaje enviado
 * correctamente!" while the academy received nothing, and nobody chases a
 * message they believe was delivered. Several tests below exist only to keep
 * that from coming back.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ContactSection from "../components/ContactSection";
import { contactForm } from "../data";

function renderForm() {
  return render(
    <MemoryRouter>
      <ContactSection />
    </MemoryRouter>,
  );
}

/** Fill every required field so submission is the only thing under test. */
async function fillRequired(user) {
  await user.type(screen.getByLabelText(/^Nombre/), "Ana");
  await user.type(screen.getByLabelText(/^Email/), "ana@example.com");
  await user.type(screen.getByLabelText(/^Teléfono/), "600123456");
  await user.type(screen.getByLabelText(/Déjanos un mensaje/), "Hola, quisiera información.");
}

function mockFetch(response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const ok = () => ({ ok: true, status: 200, json: async () => ({ success: true }) });

beforeEach(() => {
  // The component reads the CSRF token from document.cookie; Django sets it on
  // the page that carries the form.
  document.cookie = "csrftoken=test-token";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("submitting successfully", () => {
  it("posts to the Django endpoint, not to Netlify", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch(ok());
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/contact/");
    expect(options.method).toBe("POST");
  });

  it("sends every field the family filled in", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch(ok());
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const body = new URLSearchParams(fetchMock.mock.calls[0][1].body);
    expect(body.get("nombre")).toBe("Ana");
    expect(body.get("email")).toBe("ana@example.com");
    expect(body.get("telefono")).toBe("600123456");
    expect(body.get("mensaje")).toBe("Hola, quisiera información.");
  });

  it("sends the CSRF token", async () => {
    // Without it CsrfViewMiddleware answers 403 and every submission is lost.
    const user = userEvent.setup();
    const fetchMock = mockFetch(ok());
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(fetchMock.mock.calls[0][1].headers["X-CSRFToken"]).toBe("test-token");
  });

  it("shows the success message", async () => {
    const user = userEvent.setup();
    mockFetch(ok());
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    expect(await screen.findByText(contactForm.successTitle)).toBeInTheDocument();
  });

  it("does not reload the page", async () => {
    // A native form submit would navigate away and lose the success screen.
    const user = userEvent.setup();
    mockFetch(ok());
    renderForm();
    const submitEvent = vi.fn();
    document.addEventListener("submit", submitEvent);

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    await waitFor(() => expect(submitEvent).toHaveBeenCalled());
    expect(submitEvent.mock.calls[0][0].defaultPrevented).toBe(true);
    document.removeEventListener("submit", submitEvent);
  });
});

describe("when the send fails", () => {
  /**
   * THE REGRESSION. Each of these used to show the success screen.
   */
  const failures = [
    {
      name: "a 500 from the server",
      response: { ok: false, status: 500, json: async () => ({ success: false }) },
    },
    {
      name: "a 403 with an HTML body (CSRF rejection)",
      response: {
        ok: false,
        status: 403,
        json: async () => {
          throw new SyntaxError("Unexpected token <");
        },
      },
    },
    {
      name: "a 429 from the rate limiter",
      response: {
        ok: false,
        status: 429,
        json: async () => {
          throw new SyntaxError("Unexpected token");
        },
      },
    },
    {
      name: "a 200 whose body says it failed",
      response: { ok: true, status: 200, json: async () => ({ success: false, error: "Faltan campos." }) },
    },
  ];

  it.each(failures)("$name never shows the success message", async ({ response }) => {
    const user = userEvent.setup();
    mockFetch(response);
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    await screen.findByRole("alert");
    expect(screen.queryByText(contactForm.successTitle)).not.toBeInTheDocument();
  });

  it.each(failures)("$name tells the family something went wrong", async ({ response }) => {
    const user = userEvent.setup();
    mockFetch(response);
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent.trim().length).toBeGreaterThan(10);
  });

  it("surfaces the server's own message when it sends one", async () => {
    const user = userEvent.setup();
    mockFetch({
      ok: false,
      status: 400,
      json: async () => ({ success: false, error: "Faltan campos obligatorios: Email." }),
    });
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Faltan campos obligatorios: Email.");
  });

  it("survives the network being down", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(contactForm.successTitle)).not.toBeInTheDocument();
  });

  it("points the family at WhatsApp, which does work", async () => {
    // The form is one of two ways to reach the academy; a dead end here should
    // hand over to the one that is still up.
    const user = userEvent.setup();
    mockFetch({ ok: false, status: 500, json: async () => ({}) });
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/WhatsApp/i);
  });

  it("lets the family try again", async () => {
    // The button must not stay disabled after a failure, or the retry the
    // error message suggests is impossible.
    const user = userEvent.setup();
    mockFetch({ ok: false, status: 500, json: async () => ({}) });
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: contactForm.submitLabel })).toBeEnabled();
  });
});

describe("the form markup", () => {
  it("renders every field declared in data.js", () => {
    renderForm();
    for (const field of contactForm.fields) {
      expect(screen.getByLabelText(new RegExp(field.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))).toBeInTheDocument();
    }
  });

  it("marks the required fields required", () => {
    renderForm();
    for (const field of contactForm.fields.filter((f) => f.required)) {
      const input = screen.getByLabelText(new RegExp(`^${field.label}`));
      expect(input).toBeRequired();
    }
  });

  it("keeps the honeypot, hidden", () => {
    // Django reads `bot-field` and discards anything that fills it. It has to
    // stay out of sight and out of the tab order, or real people fill it in
    // and their messages are silently dropped.
    const { container } = renderForm();
    const honeypot = container.querySelector('input[name="bot-field"]');
    expect(honeypot).not.toBeNull();
    expect(honeypot).not.toBeVisible();
    expect(honeypot.getAttribute("tabindex")).toBe("-1");
  });

  it("no longer carries Netlify's form wiring", () => {
    // `netlify` and the `form-name` input mean nothing to Django; leaving them
    // would be a false hint that the form still posts somewhere it does not.
    const { container } = renderForm();
    const form = container.querySelector("form");
    expect(form.hasAttribute("netlify")).toBe(false);
    expect(container.querySelector('input[name="form-name"]')).toBeNull();
  });

  it("does not submit twice while a send is in flight", async () => {
    const user = userEvent.setup();
    let resolve;
    const fetchMock = vi.fn().mockReturnValue(new Promise((r) => { resolve = r; }));
    vi.stubGlobal("fetch", fetchMock);
    renderForm();

    await fillRequired(user);
    const button = screen.getByRole("button", { name: contactForm.submitLabel });
    await user.click(button);

    await waitFor(() => expect(screen.getByRole("button", { name: /Enviando/i })).toBeDisabled());
    expect(fetchMock).toHaveBeenCalledOnce();
    resolve(ok());
  });
});
