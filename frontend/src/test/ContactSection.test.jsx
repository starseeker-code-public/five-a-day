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
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ContactSection from "../components/ContactSection";
import { contactForm, siteConfig } from "../data";

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

  it("confirms in a real dialog, not just styled text", async () => {
    // A modal that only LOOKS modal tells a screen reader nothing happened.
    const user = userEvent.setup();
    mockFetch(ok());
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName(contactForm.successTitle);
  });

  it("clears the form so the next message starts from scratch", async () => {
    // The success state used to REPLACE the form, so sending a correction or
    // asking about a second child meant reloading the page. Now the form stays
    // behind the dialog — which only helps if it is genuinely empty.
    //
    // Clearing it with `form.reset()` LOOKS right and is not: React keeps its
    // own tracker of each input's last value, a native reset bypasses it, and
    // the next keystroke replays the old text — typing "Ana" into a visibly
    // empty box yields "AnaAna", and the doubled email then fails HTML
    // validation so the next message is never sent at all. Emptiness alone
    // does not prove the fix; retyping does. (The wait before that message can
    // actually go is the cooldown, tested below.)
    const user = userEvent.setup();
    mockFetch(ok());
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));
    await screen.findByRole("dialog");

    expect(screen.getByLabelText(/^Nombre/)).toHaveValue("");
    expect(screen.getByLabelText(/Déjanos un mensaje/)).toHaveValue("");

    await user.click(screen.getByRole("button", { name: contactForm.successCloseLabel }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.type(screen.getByLabelText(/^Nombre/), "Ana");
    expect(screen.getByLabelText(/^Nombre/)).toHaveValue("Ana");
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    mockFetch(ok());
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));
    await screen.findByRole("dialog");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("moves focus into the dialog and releases the page scroll on close", async () => {
    // Focus left behind the modal means a keyboard user tabs through a form
    // they can no longer see; a locked <body> left locked means the page never
    // scrolls again.
    const user = userEvent.setup();
    mockFetch(ok());
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));
    await screen.findByRole("dialog");

    expect(screen.getByRole("button", { name: contactForm.successCloseLabel })).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(document.body.style.overflow).not.toBe("hidden"));
  });

  it("offers WhatsApp as the faster channel", async () => {
    const user = userEvent.setup();
    mockFetch(ok());
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    const dialog = await screen.findByRole("dialog");
    const whatsapp = within(dialog).getByRole("link", { name: contactForm.successWhatsappLabel });
    expect(whatsapp).toHaveAttribute("href", siteConfig.whatsapp);
    expect(whatsapp).toHaveAttribute("rel", expect.stringContaining("noopener"));
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

describe("a connection dropped mid-submit", () => {
  /**
   * THE REGRESSION. The QA VM runs Gunicorn's sync worker with nothing in
   * front of it, so it answers `Connection: close` and every request is a new
   * TCP connection. A POST that loses the race with that teardown comes back
   * as a reset: `fetch` REJECTS, no response is ever received, and a browser
   * re-drives an idempotent GET on its own but never a POST. So a healthy site
   * told the family "comprueba tu conexión" and the enquiry was lost with no
   * trace on EITHER side — nothing reached Django, so the academy never learnt
   * that anybody had tried.
   */
  it("tries again and delivers, instead of blaming the family's connection", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(ok());
    vi.stubGlobal("fetch", fetchMock);
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    expect(await screen.findByText(contactForm.successTitle)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops after the retry rather than hammering a server that is really down", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    await screen.findByRole("alert");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  /**
   * THE GUARD THAT MATTERS, and the reason the retry is scoped to a rejection
   * rather than to failure in general. A POST is not idempotent. The endpoint
   * opens its 60-second de-duplication cooldown only once mail has gone OUT,
   * so a 502 — which is exactly the case where the SMTP send failed — is the
   * one answer that could deliver the same enquiry twice if it were retried.
   * An answered request is a DECISION; only an unanswered one is an accident.
   */
  const answered = [
    {
      name: "a 502 whose send failed",
      response: { ok: false, status: 502, json: async () => ({ success: false }) },
    },
    {
      name: "a 403 CSRF rejection",
      response: {
        ok: false,
        status: 403,
        json: async () => {
          throw new SyntaxError("Unexpected token <");
        },
      },
    },
    {
      name: "a 400 validation refusal",
      response: { ok: false, status: 400, json: async () => ({ success: false, error: "Revisa el email." }) },
    },
  ];

  it.each(answered)("never retries $name — the server answered", async ({ response }) => {
    const user = userEvent.setup();
    const fetchMock = mockFetch(response);
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    await screen.findByRole("alert");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("the cooldown between messages", () => {
  it("disables the button and counts down after a send", async () => {
    // The SERVER enforces the wait (two stacked rate_limit windows). This
    // countdown exists so a second click is visibly refused here rather than
    // answered with a 429 the visitor did nothing to earn.
    const user = userEvent.setup();
    mockFetch(ok());
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));
    await screen.findByRole("dialog");
    await user.click(screen.getByRole("button", { name: contactForm.successCloseLabel }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const button = screen.getByRole("button", { name: new RegExp(contactForm.cooldownLabel) });
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent(new RegExp(`${contactForm.cooldownSeconds - 1}|${contactForm.cooldownSeconds}`));
  });

  it("explains a 429 instead of blaming the send", async () => {
    // The rate limiter answers text/plain, so `response.json()` yields null and
    // the generic "no hemos podido enviar" would tell a throttled visitor their
    // message failed — which is both wrong and unactionable.
    const user = userEvent.setup();
    mockFetch({ ok: false, status: 429, json: async () => { throw new Error("not json"); } });
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    expect(await screen.findByRole("alert")).toHaveTextContent(contactForm.throttleMessage);
  });

  it("starts the countdown on a 429 too, so the retry is not refused again", async () => {
    const user = userEvent.setup();
    mockFetch({ ok: false, status: 429, json: async () => { throw new Error("not json"); } });
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));
    await screen.findByRole("alert");

    expect(screen.getByRole("button", { name: new RegExp(contactForm.cooldownLabel) })).toBeDisabled();
  });

  it("does not open the success dialog when the send was refused", async () => {
    const user = userEvent.setup();
    mockFetch({ ok: false, status: 400, json: async () => ({ success: false, error: "Revisa el email." }) });
    renderForm();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: contactForm.submitLabel }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Revisa el email.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // A refusal must NOT start a cooldown: the whole point is to fix the field
    // and try again immediately.
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
