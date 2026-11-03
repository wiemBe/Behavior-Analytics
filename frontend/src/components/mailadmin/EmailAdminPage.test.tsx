import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createLocaleTranslator } from "@/i18n/testTranslator";
import { ApiError, type EmailTemplate } from "@/lib/api";

import { EmailAdminPage } from "./EmailAdminPage";
import {
  ALERT_TYPES,
  customisedList,
  placeholders,
  plainList,
  preview,
  recipient,
  recipientList,
  settings,
  template,
} from "./fixtures";
import { TEST_OUTCOMES } from "./mail";

const api = vi.hoisted(() => ({
  settings: vi.fn(),
  placeholders: vi.fn(),
  templates: vi.fn(),
  template: vi.fn(),
  saveTemplate: vi.fn(),
  resetTemplate: vi.fn(),
  preview: vi.fn(),
  sendTest: vi.fn(),
  recipients: vi.fn(),
  addRecipient: vi.fn(),
  saveRecipient: vi.fn(),
  removeRecipient: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  mailAdminApi: api,
}));

const say = createLocaleTranslator("en");

function refusal(status: number, text: string, code: string, params: Record<string, unknown>) {
  return new ApiError(status, text, text, { detail: text, detail_code: code, detail_params: params });
}

function stored(key: string, over: Partial<EmailTemplate> = {}): EmailTemplate {
  return template(key, {
    customised: true,
    enabled: true,
    effective_source: "own",
    subject: `Stored subject for ${key}`,
    html_body: `<p>stored ${key}</p>`,
    updated_at: "2026-11-03T08:40:00Z",
    updated_by: "j.okafor",
    ...over,
  });
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.settings.mockResolvedValue(settings());
  api.placeholders.mockResolvedValue(placeholders());
  api.templates.mockResolvedValue(plainList());
  api.template.mockImplementation(async (key: string) => template(key));
  api.preview.mockResolvedValue(preview());
  api.recipients.mockResolvedValue(recipientList());
});

async function open() {
  render(<EmailAdminPage />);
  await screen.findByLabelText("Subject");
  await screen.findByTitle("E-mail preview");
}

const templateTable = () => screen.getByRole("table", { name: "E-mail templates" });
const html = () => screen.getByLabelText("HTML body") as HTMLTextAreaElement;
const rowFor = (name: string) => within(templateTable()).getByRole("button", { name }).closest("tr")!;

async function chooseTemplate(name: string) {
  await userEvent.click(within(templateTable()).getByRole("button", { name }));
}

function edit(field: HTMLElement, value: string) {
  fireEvent.change(field, { target: { value } });
}

describe("the first screen: the relay", () => {
  it("states the transport from /settings and nothing secret", async () => {
    api.settings.mockResolvedValue({ ...settings(), smtp_password: "hunter2" });
    await open();
    const relay = screen.getByRole("region", { hidden: true, name: "Relay" });
    expect(relay).toBeInTheDocument();
    expect(screen.getByText("smtp.relay.example:587")).toBeInTheDocument();
    expect(screen.getByText(/STARTTLS/)).toBeInTheDocument();
    expect(screen.getByText("Set by SMTP_SECURITY")).toBeInTheDocument();
    expect(
      screen.getByText("QRadar Behavior Analytics <alerts@qradar.example>"),
    ).toBeInTheDocument();
    expect(screen.getByText("Username set")).toBeInTheDocument();
    expect(screen.getByText("Not set (system trust store)")).toBeInTheDocument();
    expect(screen.getByText("Configured")).toBeInTheDocument();
    expect(screen.getByText(/never shown/)).toBeInTheDocument();
  });

  it("never draws a password field or a password value", async () => {
    api.settings.mockResolvedValue({ ...settings(), smtp_password: "hunter2", password: "hunter2" });
    await open();
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(document.body.textContent).not.toContain("hunter2");
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Recipients" }));
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(document.body.textContent).not.toContain("hunter2");
  });

  it("says plainly that no mail can leave when the relay is not configured, and turns the test send off with the reason", async () => {
    api.settings.mockResolvedValue(settings({ configured: false, host: "", username_set: false }));
    await open();
    const alert = screen.getByText("No alert e-mail can leave this site.").closest("[role=alert]")!;
    expect(alert).toHaveTextContent("SMTP_HOST");
    expect(alert).toHaveTextContent(".env");
    expect(within(alert as HTMLElement).getByRole("link", { name: "Read the guide" })).toHaveAttribute(
      "href",
      "/guide/alert-email",
    );
    expect(screen.getByText("Not configured")).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "Send test message" });
    expect(send).toBeDisabled();
    expect(screen.getByLabelText("Send to")).toBeDisabled();
    expect(send).toHaveAccessibleDescription(/No relay is configured, so nothing can be sent/);
    fireEvent.submit(send.closest("form")!);
    expect(api.sendTest).not.toHaveBeenCalled();
  });

  it("shows the insecure-login sentence as an error when a username meets security none", async () => {
    api.settings.mockResolvedValue(settings({ security: "none", username_set: true }));
    await open();
    const banner = screen.getByText("No alert e-mail is sent with this configuration.").closest("[role=alert]")!;
    expect(banner).toHaveClass("banner", "crit");
    expect(banner).toHaveTextContent(say("backend.email.test.insecure_login"));
  });

  it("raises no insecure-login error for none without a username, or for a secure mode with one", async () => {
    api.settings.mockResolvedValue(settings({ security: "none", username_set: false }));
    const { unmount } = render(<EmailAdminPage />);
    await screen.findByText("None (plain connection)");
    expect(screen.queryByText("No alert e-mail is sent with this configuration.")).not.toBeInTheDocument();
    unmount();
    api.settings.mockResolvedValue(settings({ security: "ssl", username_set: true }));
    render(<EmailAdminPage />);
    await screen.findByText(/SSL\/TLS/);
    expect(screen.queryByText("No alert e-mail is sent with this configuration.")).not.toBeInTheDocument();
  });

  it("names the derived security source", async () => {
    api.settings.mockResolvedValue(settings({ security_source: "SMTP_USE_TLS" }));
    await open();
    expect(screen.getByText("Derived from SMTP_USE_TLS")).toBeInTheDocument();
  });

  it("shows a loading state before anything is read", () => {
    api.settings.mockReturnValue(new Promise(() => {}));
    render(<EmailAdminPage />);
    expect(screen.getByText("Loading the relay settings…")).toBeInTheDocument();
  });

  it("says what failed and what to do when the settings cannot be read, and the rest still works", async () => {
    api.settings.mockRejectedValueOnce(new ApiError(500, "boom"));
    render(<EmailAdminPage />);
    const alert = await screen.findByText("Could not read the relay settings.");
    expect(alert.closest("[role=alert]")).toHaveTextContent("The backend answered with HTTP 500.");
    expect(alert.closest("[role=alert]")).toHaveTextContent("Nothing was changed.");
    await screen.findByLabelText("Subject");
    // The test send stays off: it will not guess that a relay exists.
    expect(screen.getByRole("button", { name: "Send test message" })).toBeDisabled();
    expect(screen.getByText(/relay settings could not be read/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByText("smtp.relay.example:587");
  });

  it("says the backend did not answer when there was no response", async () => {
    api.settings.mockRejectedValueOnce(new TypeError("fetch failed"));
    render(<EmailAdminPage />);
    await screen.findByText("The backend did not answer.");
  });

  it("explains a rate limit with its wait", async () => {
    api.settings.mockRejectedValueOnce(new ApiError(429, "slow", undefined, undefined, 30));
    render(<EmailAdminPage />);
    await screen.findByText(/Try again in 30 s/);
  });
});

describe("forbidden", () => {
  it("names admin:notifications and shows no part of the page", async () => {
    api.settings.mockRejectedValue(new ApiError(403, "Forbidden"));
    render(<EmailAdminPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("admin:notifications");
    expect(alert).toHaveTextContent("Nothing was changed.");
    expect(screen.queryByLabelText("Subject")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("is forbidden when any of the reads is", async () => {
    api.recipients.mockRejectedValue(new ApiError(403, "Forbidden"));
    render(<EmailAdminPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("admin:notifications");
  });
});

describe("the template list", () => {
  it("lists the default first, then each alert type, and says shipped when nothing is customised", async () => {
    await open();
    const names = within(templateTable())
      .getAllByRole("row")
      .slice(1)
      .map((row) => within(row).getAllByRole("cell")[0].textContent);
    expect(names).toEqual(["Default", ...ALERT_TYPES.map((type) => type.label)]);
    const defaultRow = rowFor("Default");
    expect(within(defaultRow).getAllByRole("cell")[1]).toHaveTextContent("No");
    expect(within(defaultRow).getAllByRole("cell")[3]).toHaveTextContent("The shipped template");
    for (const type of ALERT_TYPES) {
      expect(within(rowFor(type.label)).getAllByRole("cell")[3]).toHaveTextContent("The shipped template");
    }
  });

  it("shows customised, enabled, what is sent and who changed it when", async () => {
    api.templates.mockResolvedValue(customisedList());
    await open();
    const cells = (name: string) => within(rowFor(name)).getAllByRole("cell").map((c) => c.textContent);
    expect(cells("Default")).toEqual(["Default", "Yes", "Yes", "Its own template", expect.stringContaining("11-02 09:15 UTC")]);
    expect(cells("Default")[4]).toContain("a.rivera");
    expect(cells("Log source")[3]).toBe("The default template");
    expect(cells("WAF endpoint")[3]).toBe("Its own template");
    // Stored but switched off: customised, not enabled, and it sends the default.
    expect(cells("Custom rule").slice(1, 4)).toEqual(["Yes", "No", "The default template"]);
    expect(rowFor("Default").querySelector("time")).toHaveAttribute("datetime", "2026-11-02T09:15:00Z");
  });

  it("says what failed when the list cannot be read, and offers to try again", async () => {
    api.templates.mockRejectedValueOnce(new ApiError(502, "bad gateway"));
    render(<EmailAdminPage />);
    const alert = await screen.findByText("Could not read the templates.");
    expect(alert.closest("[role=alert]")).toHaveTextContent("HTTP 502");
    await userEvent.click(within(alert.closest("[role=alert]") as HTMLElement).getByRole("button", { name: "Try again" }));
    await screen.findByRole("table", { name: "E-mail templates" });
  });
});

describe("the editor", () => {
  it("opens the default first and a chosen type on demand, with what it sends today", async () => {
    api.template.mockImplementation(async (key: string) =>
      key === "waf_endpoint"
        ? stored(key)
        : template(key, { effective_source: key === "default" ? "shipped" : "shipped" }),
    );
    await open();
    expect(screen.getByRole("heading", { level: 2, name: "Default template" })).toBeInTheDocument();
    expect(screen.getByText(/No default template is stored, so alert e-mail sends the shipped template/)).toBeInTheDocument();
    await chooseTemplate("WAF endpoint");
    await screen.findByDisplayValue("Stored subject for waf_endpoint");
    expect(api.template).toHaveBeenCalledWith("waf_endpoint");
    expect(screen.getByRole("heading", { level: 2, name: "WAF endpoint template" })).toBeInTheDocument();
    expect(screen.getByText(/This is the stored template for WAF endpoint alerts\./)).toBeInTheDocument();
    expect(screen.getByText(/Saved/)).toHaveTextContent("by j.okafor");
    expect(within(templateTable()).getByRole("button", { name: "WAF endpoint" })).toHaveAttribute("aria-current", "true");
  });

  it("starts an uncustomised type from what it sends, enabled, and says so", async () => {
    api.templates.mockResolvedValue(customisedList());
    api.template.mockImplementation(async (key: string) =>
      key === "log_source" ? template(key, { effective_source: "default", subject: "From the default" }) : stored(key),
    );
    await open();
    await chooseTemplate("Log source");
    await screen.findByDisplayValue("From the default");
    expect(
      screen.getByText(/Log source alerts have no template of their own, so they send the default template/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Use this template")).toBeChecked();
    // Nothing differs yet.
    expect(screen.getByRole("button", { name: "Save template" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Reset|Remove this template/ })).not.toBeInTheDocument();
  });

  it("inserts a placeholder at the cursor of the field used last and puts the caret behind it", async () => {
    await open();
    const field = html();
    edit(field, "<p>AB</p>");
    field.focus();
    field.setSelectionRange(4, 4);
    await userEvent.click(screen.getByRole("button", { name: "Insert {{ alert.title }}" }));
    expect(field.value).toBe("<p>A{{ alert.title }}B</p>");
    expect(field.selectionStart).toBe(4 + "{{ alert.title }}".length);
    expect(field).toHaveFocus();
  });

  it("replaces a selection, and inserts into the subject when that was the field used last", async () => {
    await open();
    const subject = screen.getByLabelText("Subject") as HTMLInputElement;
    edit(subject, "Alert XX done");
    subject.focus();
    subject.setSelectionRange(6, 8);
    await userEvent.click(screen.getByRole("button", { name: "Insert {{ alert.severity }}" }));
    expect(subject.value).toBe("Alert {{ alert.severity }} done");
    expect(html().value).toBe(template("default").html_body);
  });

  it("lists every placeholder with its description", async () => {
    await open();
    const group = screen.getByRole("group", { name: "Placeholders" });
    expect(within(group).getAllByRole("button")).toHaveLength(4);
    expect(within(group).getByText("The alert's page on this site.")).toBeInTheDocument();
    expect(within(group).getByText(/Write it as \{\{ name \}\}/)).toBeInTheDocument();
  });

  it("says the placeholder list could not be read, instead of drawing none", async () => {
    api.placeholders.mockRejectedValue(new ApiError(500, "x"));
    await open();
    expect(screen.getByText(/placeholder list could not be read/)).toBeInTheDocument();
  });

  it("uses monospace text areas and no editor library", async () => {
    await open();
    expect(html().tagName).toBe("TEXTAREA");
    expect(html()).toHaveClass("mail-code");
    expect((screen.getByLabelText("Text body (optional)") as HTMLElement).tagName).toBe("TEXTAREA");
  });
});

describe("saving", () => {
  it("sends the draft and then shows the returned sanitised HTML, saying that the sanitiser changed it", async () => {
    api.saveTemplate.mockImplementation(async (key: string, body: { html_body: string }) =>
      stored(key, { html_body: body.html_body.replace(/<script>.*<\/script>/, ""), subject: "New subject" }),
    );
    await open();
    edit(screen.getByLabelText("Subject"), "New subject");
    edit(html(), "<p>kept</p><script>alert(1)</script>");
    await userEvent.click(screen.getByRole("button", { name: "Save template" }));
    await screen.findByText("Template saved.");
    expect(api.saveTemplate).toHaveBeenCalledWith("default", {
      subject: "New subject",
      html_body: "<p>kept</p><script>alert(1)</script>",
      text_body: null,
      enabled: true,
    });
    expect(html().value).toBe("<p>kept</p>");
    expect(
      screen.getByText(/The sanitiser changed the HTML you typed\. The editor now shows what is stored/),
    ).toBeInTheDocument();
    // What is shown is what is stored: nothing is unsaved.
    expect(screen.getByRole("button", { name: "Save template" })).toBeDisabled();
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    // The list was read again.
    await waitFor(() => expect(api.templates).toHaveBeenCalledTimes(2));
  });

  it("says when the sanitiser kept the HTML as typed", async () => {
    api.saveTemplate.mockImplementation(async (key: string, body: { html_body: string }) =>
      stored(key, { html_body: body.html_body }),
    );
    await open();
    edit(html(), "<p>fine</p>");
    await userEvent.click(screen.getByRole("button", { name: "Save template" }));
    await screen.findByText("The sanitiser kept the HTML as typed.");
    expect(screen.queryByText(/changed the HTML you typed/)).not.toBeInTheDocument();
  });

  it("sends a typed text body, and null for a blank one", async () => {
    api.saveTemplate.mockImplementation(async (key: string) => stored(key));
    await open();
    edit(screen.getByLabelText("Text body (optional)"), "Plain version");
    edit(screen.getByLabelText("Use this template"), "x");
    await userEvent.click(screen.getByLabelText("Use this template"));
    await userEvent.click(screen.getByRole("button", { name: "Save template" }));
    await screen.findByText("Template saved.");
    expect(api.saveTemplate.mock.calls[0][1]).toMatchObject({ text_body: "Plain version", enabled: false });
  });

  it("says what was refused, with the coded sentence, and keeps the draft", async () => {
    api.saveTemplate.mockRejectedValue(
      refusal(
        422,
        "The template uses the placeholder alert.unknown, which is not available. Use one of the names the placeholder list shows.",
        "email.template.unknown_placeholder",
        { name: "alert.unknown" },
      ),
    );
    await open();
    edit(html(), "<p>{{ alert.unknown }}</p>");
    await userEvent.click(screen.getByRole("button", { name: "Save template" }));
    const alert = (await screen.findByText("The template was not saved.")).closest("[role=alert]")!;
    expect(alert).toHaveTextContent("alert.unknown");
    expect(html().value).toBe("<p>{{ alert.unknown }}</p>");
    expect(screen.getAllByText("Unsaved changes").length).toBeGreaterThanOrEqual(2);
  });
});

describe("unsaved changes", () => {
  it("are flagged on the heading, the row and the tab", async () => {
    await open();
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    edit(screen.getByLabelText("Subject"), "Changed");
    expect(screen.getAllByText("Unsaved changes").length).toBeGreaterThanOrEqual(2);
    expect(within(rowFor("Default")).getByText("Unsaved changes")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Templates (unsaved changes)" })).toBeInTheDocument();
  });

  it("survive switching to the other tab and back", async () => {
    await open();
    edit(html(), "<p>half written</p>");
    await userEvent.click(screen.getByRole("tab", { name: /Recipients/ }));
    expect(screen.queryByLabelText("HTML body")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /Templates/ }));
    expect(html().value).toBe("<p>half written</p>");
    expect(screen.getByRole("tab", { name: "Templates (unsaved changes)" })).toBeInTheDocument();
  });

  it("survive choosing another template", async () => {
    api.template.mockImplementation(async (key: string) => template(key));
    await open();
    edit(screen.getByLabelText("Subject"), "Default draft");
    await chooseTemplate("Log source");
    await screen.findByRole("heading", { level: 2, name: "Log source template" });
    expect(within(rowFor("Default")).getByText("Unsaved changes")).toBeInTheDocument();
    await chooseTemplate("Default");
    expect(screen.getByLabelText("Subject")).toHaveValue("Default draft");
    expect(api.template).toHaveBeenCalledTimes(2);
  });

  it("can be discarded", async () => {
    await open();
    edit(screen.getByLabelText("Subject"), "Changed");
    await userEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(screen.getByLabelText("Subject")).toHaveValue(template("default").subject);
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
  });

  it("ask before the page is closed or reloaded, and only then", async () => {
    await open();
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    edit(screen.getByLabelText("Subject"), "Changed");
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });

  it("ask before a link to another page of the application is followed", async () => {
    await open();
    const link = document.createElement("a");
    link.href = "/alerts";
    link.textContent = "Alerts";
    document.body.appendChild(link);
    // The router never sees a click the guard stopped. A listener behind it records what the guard
    // decided and stops jsdom from trying to navigate.
    const seen: boolean[] = [];
    const behind = (event: Event) => {
      seen.push(event.defaultPrevented);
      event.preventDefault();
    };
    document.addEventListener("click", behind);
    const confirm = vi.spyOn(window, "confirm");
    try {
      confirm.mockReturnValue(false);
      fireEvent.click(link);
      expect(confirm).not.toHaveBeenCalled();
      edit(screen.getByLabelText("Subject"), "Changed");
      fireEvent.click(link);
      expect(confirm).toHaveBeenCalledWith("A template has unsaved changes. Leave this page and lose them?");
      confirm.mockReturnValue(true);
      fireEvent.click(link);
      expect(confirm).toHaveBeenCalledTimes(2);
      // MAIL-3: a confirmed click is followed once the guard's history entry is off the stack, so
      // it reaches the router a moment later instead of in the same call.
      await waitFor(() => expect(seen).toEqual([false, false]));
      // A stay is a click nobody downstream sees at all.
      confirm.mockReturnValue(false);
      fireEvent.click(link);
      expect(seen).toEqual([false, false]);
    } finally {
      document.removeEventListener("click", behind);
      link.remove();
    }
  });

  it("do not stop a link when nothing is unsaved, a link to the same page, or another site", async () => {
    await open();
    edit(screen.getByLabelText("Subject"), "Changed");
    const same = document.createElement("a");
    same.href = window.location.href;
    const other = document.createElement("a");
    other.href = "https://example.org/x";
    document.body.append(same, other);
    const behind = (event: Event) => event.preventDefault();
    document.addEventListener("click", behind);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    try {
      fireEvent.click(same);
      fireEvent.click(other);
      expect(confirm).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener("click", behind);
      same.remove();
      other.remove();
    }
  });
});

describe("reset", () => {
  it("asks first, and says the default goes back to the shipped template", async () => {
    api.templates.mockResolvedValue(customisedList());
    api.template.mockImplementation(async (key: string) => stored(key));
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Reset to the shipped template" }));
    const dialog = screen.getByText("Reset the Default template?").closest("[role=alert]") as HTMLElement;
    expect(dialog).toHaveTextContent("goes back to the template shipped with the product");
    expect(dialog).toHaveTextContent("Types with a template of their own keep it.");
    expect(api.resetTemplate).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Keep it" }));
    expect(api.resetTemplate).not.toHaveBeenCalled();
    expect(screen.queryByText("Reset the Default template?")).not.toBeInTheDocument();
  });

  it("says a type falls back to the default template when a default is in use", async () => {
    api.templates.mockResolvedValue(customisedList());
    api.template.mockImplementation(async (key: string) => stored(key));
    await open();
    await chooseTemplate("WAF endpoint");
    await screen.findByDisplayValue("Stored subject for waf_endpoint");
    await userEvent.click(screen.getByRole("button", { name: "Remove this template" }));
    const dialog = screen.getByText("Reset the WAF endpoint template?").closest("[role=alert]") as HTMLElement;
    expect(dialog).toHaveTextContent("These alerts then send the default template. The default is not changed.");
  });

  it("says a type falls back to the shipped template when no default is in use", async () => {
    const list = customisedList();
    list.templates[0] = { ...list.templates[0], customised: false, enabled: false, updated_at: null, updated_by: null };
    api.templates.mockResolvedValue(list);
    api.template.mockImplementation(async (key: string) => (key === "default" ? template(key) : stored(key)));
    await open();
    await chooseTemplate("WAF endpoint");
    await screen.findByDisplayValue("Stored subject for waf_endpoint");
    await userEvent.click(screen.getByRole("button", { name: "Remove this template" }));
    expect(screen.getByText(/These alerts then send the shipped template/)).toBeInTheDocument();
  });

  it("adds that unsaved edits are lost too, when there are any", async () => {
    api.templates.mockResolvedValue(customisedList());
    api.template.mockImplementation(async (key: string) => stored(key));
    await open();
    edit(screen.getByLabelText("Subject"), "Changed");
    await userEvent.click(screen.getByRole("button", { name: "Reset to the shipped template" }));
    expect(screen.getByText(/Your unsaved changes to it are lost as well\./)).toBeInTheDocument();
  });

  it("deletes the row on confirmation, then shows what the key sends now", async () => {
    api.templates.mockResolvedValue(customisedList());
    let deleted = false;
    api.template.mockImplementation(async (key: string) =>
      deleted ? template(key, { subject: "Shipped subject" }) : stored(key),
    );
    api.resetTemplate.mockImplementation(async () => {
      deleted = true;
    });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Reset to the shipped template" }));
    await userEvent.click(screen.getByRole("button", { name: "Reset template" }));
    await screen.findByDisplayValue("Shipped subject");
    expect(api.resetTemplate).toHaveBeenCalledWith("default");
    expect(screen.queryByText("Reset the Default template?")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset to the shipped template" })).not.toBeInTheDocument();
    await waitFor(() => expect(api.templates).toHaveBeenCalledTimes(2));
  });

  it("keeps the confirmation open and says why when the delete fails", async () => {
    api.templates.mockResolvedValue(customisedList());
    api.template.mockImplementation(async (key: string) => stored(key));
    api.resetTemplate.mockRejectedValue(new ApiError(500, "boom"));
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Reset to the shipped template" }));
    await userEvent.click(screen.getByRole("button", { name: "Reset template" }));
    const dialog = (await screen.findByText("Reset the Default template?")).closest("[role=alert]") as HTMLElement;
    await waitFor(() => expect(dialog.querySelectorAll("li").length).toBeGreaterThan(0));
    expect(screen.getByLabelText("Subject")).toHaveValue("Stored subject for default");
  });
});

describe("the preview", () => {
  it("renders the returned HTML in a frame with an empty sandbox and no allow-* token", async () => {
    api.preview.mockResolvedValue(preview({ html: "<p>Rendered body</p>" }));
    await open();
    const frame = screen.getByTitle("E-mail preview") as HTMLIFrameElement;
    expect(frame.tagName).toBe("IFRAME");
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.outerHTML).not.toMatch(/allow-/);
    expect(frame.getAttribute("srcdoc")).toBe("<p>Rendered body</p>");
    expect(frame).not.toHaveAttribute("src");
  });

  it("builds the preview from the unsaved editor and the chosen sample alert", async () => {
    await open();
    expect(api.preview).toHaveBeenLastCalledWith(
      expect.objectContaining({ alert_type: null, subject: template("default").subject, enabled: true }),
    );
    edit(html(), "<p>unsaved {{ alert.title }}</p>");
    await userEvent.selectOptions(screen.getByLabelText("Sample alert"), "waf_endpoint");
    await waitFor(() =>
      expect(api.preview).toHaveBeenLastCalledWith({
        subject: template("default").subject,
        html_body: "<p>unsaved {{ alert.title }}</p>",
        text_body: null,
        enabled: true,
        alert_type: "waf_endpoint",
      }),
    );
  });

  it("opens a type's editor with that type's sample alert", async () => {
    await open();
    await chooseTemplate("IPS entity");
    await waitFor(() => expect(api.preview).toHaveBeenLastCalledWith(expect.objectContaining({ alert_type: "ips_entity" })));
    expect(screen.getByLabelText("Sample alert")).toHaveValue("ips_entity");
  });

  it("shows the subject and the text part", async () => {
    await open();
    expect(screen.getByText("[HIGH] Alert: this is a preview (OPENED)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Text part" }));
    const text = screen.getByLabelText("Text part of the e-mail");
    expect(text).toHaveTextContent("Alert: this is a preview");
    expect(text).toHaveTextContent("https://qradar.example/alerts/1");
    expect(screen.queryByTitle("E-mail preview")).not.toBeInTheDocument();
  });

  it("says the preview is out of date once the editor has changed, and updates on request", async () => {
    await open();
    expect(screen.queryByText("Out of date")).not.toBeInTheDocument();
    edit(screen.getByLabelText("Subject"), "Another subject");
    expect(screen.getByText("Out of date")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Update preview" }));
    await waitFor(() => expect(screen.queryByText("Out of date")).not.toBeInTheDocument());
    expect(api.preview).toHaveBeenLastCalledWith(expect.objectContaining({ subject: "Another subject" }));
  });

  it("says why it could not be built, with the coded sentence, and draws no stale frame", async () => {
    await open();
    api.preview.mockRejectedValue(
      refusal(
        422,
        "The template uses the placeholder x.y, which is not available. Use one of the names the placeholder list shows.",
        "email.template.unknown_placeholder",
        { name: "x.y" },
      ),
    );
    edit(html(), "<p>{{ x.y }}</p>");
    await userEvent.click(screen.getByRole("button", { name: "Update preview" }));
    const alert = (await screen.findByText("The preview could not be built.")).closest("[role=alert]")!;
    expect(alert).toHaveTextContent("x.y");
    expect(alert).toHaveTextContent("Nothing was saved.");
    expect(screen.queryByTitle("E-mail preview")).not.toBeInTheDocument();
  });

  it("keeps the last preview on screen while the next one is built", async () => {
    await open();
    let finish: (value: ReturnType<typeof preview>) => void = () => {};
    api.preview.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    await userEvent.click(screen.getByRole("button", { name: "Update preview" }));
    expect(screen.getByRole("button", { name: "Updating" })).toBeDisabled();
    expect(screen.getByTitle("E-mail preview")).toBeInTheDocument();
    await act(async () => finish(preview({ html: "<p>next</p>" })));
    expect(screen.getByTitle("E-mail preview").getAttribute("srcdoc")).toBe("<p>next</p>");
  });
});

describe("the test send", () => {
  async function send(to = "me@example.org") {
    await userEvent.type(screen.getByLabelText("Send to"), to);
    await userEvent.click(screen.getByRole("button", { name: "Send test message" }));
  }

  it("sends the unsaved editor to the typed mailbox and says the relay accepted it", async () => {
    api.sendTest.mockResolvedValue({
      ok: true,
      outcome: { code: "email.test.sent", params: {}, message: "ignored" },
    });
    await open();
    edit(html(), "<p>unsaved</p>");
    await send();
    const banner = (await screen.findByText("The relay accepted the test message.")).closest("[role=status]")!;
    expect(banner).toHaveClass("ok");
    expect(banner).toHaveTextContent(say("backend.email.test.sent"));
    expect(banner).toHaveTextContent("Check the mailbox, including its junk folder.");
    expect(api.sendTest).toHaveBeenCalledWith({
      subject: template("default").subject,
      html_body: "<p>unsaved</p>",
      text_body: null,
      enabled: true,
      alert_type: null,
      to: "me@example.org",
    });
  });

  it("is off until a mailbox is typed, and off while sending", async () => {
    await open();
    const button = screen.getByRole("button", { name: "Send test message" });
    expect(button).toBeDisabled();
    let finish: (value: unknown) => void = () => {};
    api.sendTest.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    await userEvent.type(screen.getByLabelText("Send to"), "me@example.org");
    expect(button).toBeEnabled();
    await userEvent.click(button);
    expect(screen.getByRole("button", { name: "Sending" })).toBeDisabled();
    await act(async () =>
      finish({ ok: true, outcome: { code: "email.test.sent", params: {}, message: "" } }),
    );
    expect(screen.getByRole("button", { name: "Send test message" })).toBeEnabled();
  });

  it.each(TEST_OUTCOMES.filter((name) => name !== "sent"))(
    "words the outcome %s as its English sentence with what to do next",
    async (name) => {
      api.sendTest.mockResolvedValue({
        ok: false,
        outcome: { code: `email.test.${name}`, params: {}, message: "the relay said something rude" },
      });
      await open();
      await send();
      const alert = (await screen.findByText("The test message was not sent.")).closest("[role=alert]") as HTMLElement;
      expect(alert).toHaveClass("crit");
      expect(alert).toHaveTextContent(say(`backend.email.test.${name}`));
      expect(alert).toHaveTextContent(`Code: email.test.${name}`);
      expect(alert).not.toHaveTextContent("rude");
      expect(alert.querySelectorAll("p").length).toBeGreaterThanOrEqual(4);
    },
  );

  it("shows the backend's own sentence for a code it does not know, never a blank", async () => {
    api.sendTest.mockResolvedValue({
      ok: false,
      outcome: { code: "email.test.greylisted", params: {}, message: "The relay asked us to retry later." },
    });
    await open();
    await send();
    const alert = (await screen.findByText("The test message was not sent.")).closest("[role=alert]")!;
    expect(alert).toHaveTextContent("The relay asked us to retry later.");
    expect(alert).toHaveTextContent("Code: email.test.greylisted");
  });

  it("explains a 429 with the limit from the settings and when to retry", async () => {
    api.settings.mockResolvedValue(settings({ test_rate_limit: "3/minute" }));
    api.sendTest.mockRejectedValue(new ApiError(429, "Too Many Requests", undefined, undefined, 42));
    await open();
    await send();
    const alert = (await screen.findByText("Too many test messages.")).closest("[role=alert]")!;
    expect(alert).toHaveTextContent("Test messages are limited to 3/minute.");
    expect(alert).toHaveTextContent("Try again in 42 s.");
  });

  it("says to wait a minute when a 429 names no wait", async () => {
    api.sendTest.mockRejectedValue(new ApiError(429, "Too Many Requests"));
    await open();
    await send();
    const alert = (await screen.findByText("Too many test messages.")).closest("[role=alert]")!;
    expect(alert).toHaveTextContent("Test messages are limited to 5/minute.");
    expect(alert).toHaveTextContent("Wait a minute, then send again.");
  });

  it("shows the coded sentence of a refused mailbox", async () => {
    api.sendTest.mockRejectedValue(
      refusal(
        422,
        "nope is not a plain e-mail address such as soc@example.org.",
        "email.recipient.address_invalid",
        { address: "nope" },
      ),
    );
    await open();
    await send("nope");
    const alert = (await screen.findByText("The test message was not sent.")).closest("[role=alert]")!;
    expect(alert).toHaveTextContent("nope is not a plain e-mail address such as soc@example.org.");
  });

  it("clears the last outcome when another template is chosen", async () => {
    api.sendTest.mockResolvedValue({
      ok: true,
      outcome: { code: "email.test.sent", params: {}, message: "" },
    });
    await open();
    await send();
    await screen.findByText("The relay accepted the test message.");
    await chooseTemplate("Log source");
    expect(screen.queryByText("The relay accepted the test message.")).not.toBeInTheDocument();
  });
});

describe("recipients", () => {
  async function recipientsTab() {
    await open();
    await userEvent.click(screen.getByRole("tab", { name: "Recipients" }));
  }
  const table = () => screen.getByRole("table", { name: "Stored recipients" });

  it("lists address, minimum severity, alert types or every type, enabled, and who changed it when", async () => {
    await recipientsTab();
    const rows = within(table()).getAllByRole("row").slice(1);
    const cells = rows.map((row) => within(row).getAllByRole("cell").map((c) => c.textContent));
    expect(cells[0].slice(0, 4)).toEqual(["soc@example.org", "Low", "Every type", "Yes"]);
    expect(cells[0][4]).toContain("a.rivera");
    expect(cells[1].slice(0, 4)).toEqual(["oncall@example.org", "High", "WAF endpoint, IPS entity", "Yes"]);
    expect(cells[2].slice(0, 4)).toEqual(["audit@example.org", "Critical", "Every type", "No"]);
    expect(rows[0].querySelector("time")).toHaveAttribute("datetime", "2026-11-02T10:00:00Z");
  });

  it("lists the addresses from .env apart, read only, with their rule", async () => {
    await recipientsTab();
    const env = screen.getByRole("list", { name: "From .env" });
    expect(env).toHaveTextContent("noc@example.org");
    expect(screen.getByText(/They receive every alert, LOW and above, and cannot be changed here/)).toBeInTheDocument();
    expect(within(env).queryByRole("button")).not.toBeInTheDocument();
    expect(within(table()).queryByText("noc@example.org")).not.toBeInTheDocument();
  });

  it("says that no alert e-mail is sent when there is no stored and no .env recipient", async () => {
    api.recipients.mockResolvedValue(recipientList({ recipients: [], from_env: [] }));
    await recipientsTab();
    const banner = screen.getByText("No alert e-mail is sent.").closest(".banner") as HTMLElement;
    expect(banner).toHaveTextContent("No recipient is stored here and NOTIFY_EMAIL_RECIPIENTS in .env is empty.");
    expect(screen.queryByRole("table", { name: "Stored recipients" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add recipient" })).toBeInTheDocument();
  });

  it("says so too when every stored recipient is switched off and .env has none", async () => {
    api.recipients.mockResolvedValue(
      recipientList({ recipients: [recipient({ enabled: false })], from_env: [] }),
    );
    await recipientsTab();
    expect(screen.getByText("No alert e-mail is sent.").closest(".banner")).toHaveTextContent(
      "Every stored recipient is switched off",
    );
  });

  it("does not claim silence when only .env has recipients", async () => {
    api.recipients.mockResolvedValue(recipientList({ recipients: [], from_env: ["noc@example.org"] }));
    await recipientsTab();
    expect(screen.queryByText("No alert e-mail is sent.")).not.toBeInTheDocument();
    expect(screen.getByText("No recipient is stored in the application.")).toBeInTheDocument();
  });

  it("says what failed when the recipients cannot be read, and offers to try again", async () => {
    api.recipients.mockRejectedValueOnce(new ApiError(500, "x"));
    await open();
    await userEvent.click(screen.getByRole("tab", { name: "Recipients" }));
    const alert = (await screen.findByText("Could not read the recipients.")).closest("[role=alert]") as HTMLElement;
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    await screen.findByRole("table", { name: "Stored recipients" });
  });

  it("adds a recipient for every type", async () => {
    api.addRecipient.mockResolvedValue(recipient({ id: "r-9", address: "new@example.org" }));
    await recipientsTab();
    await userEvent.click(screen.getByRole("button", { name: "Add recipient" }));
    await userEvent.type(screen.getByLabelText("E-mail address"), "new@example.org");
    await userEvent.selectOptions(screen.getByLabelText("Minimum severity"), "HIGH");
    const form = screen.getByRole("form", { name: "Add a recipient" });
    await userEvent.click(form.querySelector("button[type=submit]")!);
    await waitFor(() =>
      expect(api.addRecipient).toHaveBeenCalledWith({
        address: "new@example.org",
        min_severity: "HIGH",
        alert_types: null,
        enabled: true,
      }),
    );
    await waitFor(() => expect(api.recipients).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("form", { name: "Add a recipient" })).not.toBeInTheDocument();
  });

  it("adds a recipient limited to the ticked alert types, and asks for at least one", async () => {
    api.addRecipient.mockResolvedValue(recipient({ id: "r-9" }));
    await recipientsTab();
    await userEvent.click(screen.getByRole("button", { name: "Add recipient" }));
    const form = screen.getByRole("form", { name: "Add a recipient" });
    await userEvent.type(within(form).getByLabelText("E-mail address"), "x@example.org");
    await userEvent.click(within(form).getByLabelText("Only these alert types"));
    expect(within(form).getByText("Choose at least one alert type.")).toBeInTheDocument();
    const submit = form.querySelector("button[type=submit]") as HTMLButtonElement;
    expect(submit).toBeDisabled();
    await userEvent.click(within(form).getByLabelText("WAF endpoint"));
    await userEvent.click(within(form).getByLabelText("Custom rule"));
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    await waitFor(() =>
      expect(api.addRecipient).toHaveBeenCalledWith({
        address: "x@example.org",
        min_severity: "LOW",
        alert_types: ["waf_endpoint", "custom_rule"],
        enabled: true,
      }),
    );
  });

  it("puts the 409 duplicate sentence under the address and keeps the form", async () => {
    api.addRecipient.mockRejectedValue(
      refusal(
        409,
        "soc@example.org is already a recipient. Edit the existing entry instead.",
        "email.recipient.duplicate",
        { address: "soc@example.org" },
      ),
    );
    await recipientsTab();
    await userEvent.click(screen.getByRole("button", { name: "Add recipient" }));
    const form = screen.getByRole("form", { name: "Add a recipient" });
    await userEvent.type(within(form).getByLabelText("E-mail address"), "soc@example.org");
    await userEvent.click(form.querySelector("button[type=submit]")!);
    const field = await within(form).findByText("soc@example.org is already a recipient. Edit the existing entry instead.");
    expect(field).toHaveAttribute("role", "alert");
    expect(within(form).getByLabelText("E-mail address")).toHaveAttribute("aria-invalid", "true");
    expect(within(form).getByLabelText("E-mail address")).toHaveAccessibleDescription(/already a recipient/);
    expect(within(form).getByLabelText("E-mail address")).toHaveValue("soc@example.org");
  });

  it("puts a 422 coded address error under the address", async () => {
    api.addRecipient.mockRejectedValue(
      refusal(
        422,
        "a b is not a plain e-mail address such as soc@example.org.",
        "email.recipient.address_invalid",
        { address: "a b" },
      ),
    );
    await recipientsTab();
    await userEvent.click(screen.getByRole("button", { name: "Add recipient" }));
    const form = screen.getByRole("form", { name: "Add a recipient" });
    await userEvent.type(within(form).getByLabelText("E-mail address"), "a b");
    await userEvent.click(form.querySelector("button[type=submit]")!);
    expect(await within(form).findByText(/is not a plain e-mail address/)).toHaveAttribute("role", "alert");
  });

  it("puts the unknown-type reasons under the alert types", async () => {
    api.addRecipient.mockRejectedValue(
      new ApiError(422, "x", ["made_up is not an alert type a recipient can be limited to."], {
        detail_codes: [{ code: "email.recipient.alert_type_unknown", params: { name: "made_up" } }],
      }),
    );
    await recipientsTab();
    await userEvent.click(screen.getByRole("button", { name: "Add recipient" }));
    const form = screen.getByRole("form", { name: "Add a recipient" });
    await userEvent.type(within(form).getByLabelText("E-mail address"), "a@example.org");
    await userEvent.click(form.querySelector("button[type=submit]")!);
    const error = await within(form).findByText(/is not an alert type a recipient can be limited to/);
    expect(error.closest("fieldset")).not.toBeNull();
  });

  it("edits a recipient with its values in the form, and saves them by id", async () => {
    api.saveRecipient.mockResolvedValue(recipient());
    await recipientsTab();
    await userEvent.click(screen.getByRole("button", { name: "Edit oncall@example.org" }));
    const form = screen.getByRole("form", { name: "Edit a recipient" });
    expect(within(form).getByLabelText("E-mail address")).toHaveValue("oncall@example.org");
    expect(within(form).getByLabelText("Minimum severity")).toHaveValue("HIGH");
    expect(within(form).getByLabelText("Only these alert types")).toBeChecked();
    expect(within(form).getByLabelText("WAF endpoint")).toBeChecked();
    expect(within(form).getByLabelText("Log source")).not.toBeChecked();
    await userEvent.click(within(form).getByLabelText("Send alert e-mail to this address"));
    await userEvent.click(within(form).getByRole("button", { name: "Save recipient" }));
    await waitFor(() =>
      expect(api.saveRecipient).toHaveBeenCalledWith("r-2", {
        address: "oncall@example.org",
        min_severity: "HIGH",
        alert_types: ["waf_endpoint", "ips_entity"],
        enabled: false,
      }),
    );
  });

  it("asks before removing, names the address, and removes only on confirmation", async () => {
    api.removeRecipient.mockResolvedValue(undefined);
    await recipientsTab();
    await userEvent.click(screen.getByRole("button", { name: "Remove audit@example.org" }));
    const dialog = screen.getByText("Remove audit@example.org?").closest("[role=alert]") as HTMLElement;
    expect(dialog).toHaveTextContent("stops receiving alert e-mail at once");
    expect(api.removeRecipient).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Keep it" }));
    expect(api.removeRecipient).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Remove audit@example.org" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove recipient" }));
    await waitFor(() => expect(api.removeRecipient).toHaveBeenCalledWith("r-3"));
    await waitFor(() => expect(api.recipients).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("Remove audit@example.org?")).not.toBeInTheDocument();
  });

  it("says why a removal failed and keeps the question open", async () => {
    api.removeRecipient.mockRejectedValue(new ApiError(500, "boom"));
    await recipientsTab();
    await userEvent.click(screen.getByRole("button", { name: "Remove audit@example.org" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove recipient" }));
    const dialog = (await screen.findByText("Remove audit@example.org?")).closest("[role=alert]") as HTMLElement;
    await waitFor(() => expect(dialog.querySelectorAll("li").length).toBe(1));
  });
});

describe("the page as a whole", () => {
  it("has no heading level 1 of its own, two tabs, and no exclamation mark", async () => {
    await open();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
    const tabs = within(screen.getByRole("tablist", { name: "Alert e-mail sections" }));
    expect(tabs.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Templates", "Recipients"]);
    expect(document.body.textContent).not.toMatch(/!|real-time|\blive\b/i);
  });

  it("draws every time as UTC", async () => {
    api.templates.mockResolvedValue(customisedList());
    api.template.mockImplementation(async (key: string) => stored(key));
    await open();
    for (const time of document.querySelectorAll("time")) {
      expect(time.textContent).toMatch(/UTC$/);
    }
  });
});

