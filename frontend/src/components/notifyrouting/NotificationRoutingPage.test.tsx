import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type DestinationTestResult } from "@/lib/api";

import { destination, rule } from "./fixtures";
import { NotificationRoutingPage } from "./NotificationRoutingPage";

const api = vi.hoisted(() => ({
  destinations: vi.fn(),
  createDestination: vi.fn(),
  saveDestination: vi.fn(),
  deleteDestination: vi.fn(),
  testDestination: vi.fn(),
  rules: vi.fn(),
  createRule: vi.fn(),
  saveRule: vi.fn(),
  deleteRule: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  notificationRoutingApi: api,
}));

function coded(status: number, text: string, code: string, params: Record<string, unknown> = {}) {
  return new ApiError(status, text, text, { detail: text, detail_code: code, detail_params: params });
}

function testResult(over: Partial<DestinationTestResult> = {}): DestinationTestResult {
  return {
    ok: true,
    status: 204,
    result: "ok",
    outcome: {
      code: "notifications.test_ok",
      params: {},
      message: "The destination accepted the test webhook.",
    },
    ...over,
  };
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.destinations.mockResolvedValue([destination()]);
  api.rules.mockResolvedValue([rule()]);
});

function destinationRow(): HTMLElement {
  const table = screen.getByRole("table", { name: "Webhook destinations" });
  return within(table).getByText("SOAR").closest("tr") as HTMLElement;
}

async function loaded() {
  render(<NotificationRoutingPage />);
  await screen.findByRole("table", { name: "Routing rules" });
}

describe("states", () => {
  it("shows a loading line in each section while the reads are pending", () => {
    api.rules.mockReturnValue(new Promise(() => {}));
    api.destinations.mockReturnValue(new Promise(() => {}));
    render(<NotificationRoutingPage />);
    expect(screen.getByText("Loading the routing rules.")).toBeInTheDocument();
    expect(screen.getByText("Loading the webhook destinations.")).toBeInTheDocument();
  });

  it("says what is empty and that the .env channels still work", async () => {
    api.rules.mockResolvedValue([]);
    api.destinations.mockResolvedValue([]);
    render(<NotificationRoutingPage />);
    expect(
      await screen.findByText(
        "No webhook destinations. Alerts still use the .env channels and e-mail recipients.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No routing rules. Alerts still use the .env channels and e-mail recipients."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    // A rule needs a destination: with none, the add button is off and says why.
    const add = screen.getByRole("button", { name: "Add rule" });
    expect(add).toBeDisabled();
    expect(screen.getByText(/Add a destination first/)).toBeInTheDocument();
  });

  it("names what failed and retries only that part", async () => {
    api.rules.mockRejectedValueOnce(new ApiError(500, "boom"));
    const user = userEvent.setup();
    render(<NotificationRoutingPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not read the routing rules.");
    expect(alert).toHaveTextContent("The backend answered with HTTP 500.");
    expect(alert).toHaveTextContent("Nothing was changed.");
    expect(alert).not.toHaveTextContent("boom");
    // The destinations stay on screen.
    expect(await screen.findByText("https://soar.example")).toBeInTheDocument();
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table", { name: "Routing rules" })).toBeInTheDocument();
    expect(api.rules).toHaveBeenCalledTimes(2);
    expect(api.destinations).toHaveBeenCalledTimes(1);
  });

  it("names the permission when forbidden and shows no rows", async () => {
    api.rules.mockRejectedValue(new ApiError(403, "no"));
    api.destinations.mockRejectedValue(new ApiError(403, "no"));
    render(<NotificationRoutingPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("admin:notifications");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("SOAR")).not.toBeInTheDocument();
  });
});

describe("forbidden on one read", () => {
  it("is forbidden when only the destinations read is refused, and shows no rules either", async () => {
    api.destinations.mockRejectedValue(new ApiError(403, "no"));
    render(<NotificationRoutingPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("admin:notifications");
    expect(screen.queryByText("Critical to SOAR")).not.toBeInTheDocument();
  });

  it("is forbidden when only the rules read is refused, and shows no destinations either", async () => {
    api.rules.mockRejectedValue(new ApiError(403, "no"));
    render(<NotificationRoutingPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("admin:notifications");
    expect(screen.queryByText("https://soar.example")).not.toBeInTheDocument();
  });
});

describe("destinations table", () => {
  it("shows the redacted address, signing, header names and the last test in UTC", async () => {
    await loaded();
    const row = destinationRow();
    expect(within(row).getByText("https://soar.example")).toBeInTheDocument();
    expect(within(row).getByText("Signed")).toBeInTheDocument();
    expect(within(row).getByText("Headers: X-Team")).toBeInTheDocument();
    expect(row).toHaveTextContent("ok 204");
    expect(row).toHaveTextContent("UTC");
    expect(row).not.toHaveTextContent("HTTP allowed");
  });

  it("flags http and private addresses visibly and a failing test with its class", async () => {
    api.destinations.mockResolvedValue([
      destination({
        allow_http: true,
        allow_private: true,
        secret_set: false,
        header_names: [],
        last_test_status: 503,
        last_test_class: "http_status",
      }),
    ]);
    await loaded();
    const row = destinationRow();
    expect(within(row).getByText("HTTP allowed")).toHaveClass("warn");
    expect(within(row).getByText("Private addresses allowed")).toHaveClass("warn");
    expect(within(row).getByText("Not signed")).toBeInTheDocument();
    expect(within(row).getByText("http_status 503")).toHaveClass("crit");
  });

  it("says a destination was never tested instead of drawing a result", async () => {
    api.destinations.mockResolvedValue([
      destination({ last_test_at: null, last_test_status: null, last_test_class: null }),
    ]);
    await loaded();
    expect(screen.getByText("Not tested")).toBeInTheDocument();
  });

  it("disables adding at ten destinations and says so", async () => {
    api.destinations.mockResolvedValue(
      Array.from({ length: 10 }, (_, i) => destination({ id: `d-${i}`, name: `D${i}` })),
    );
    await loaded();
    expect(screen.getByRole("button", { name: "Add destination" })).toBeDisabled();
    expect(screen.getByText(/maximum of 10 destinations/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Webhook destinations (10 of 10)" })).toBeInTheDocument();
  });
});

describe("destination form: write-only fields", () => {
  it("never puts the stored URL, secret or header values into a field", async () => {
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Edit destination SOAR" }));
    const form = screen.getByRole("form", { name: "Edit a webhook destination" });
    expect(within(form).getByText("Stored address: https://soar.example")).toBeInTheDocument();
    expect(within(form).getByLabelText("Replace URL")).toHaveValue("");
    expect(within(form).getByText("A secret is set.")).toBeInTheDocument();
    expect(within(form).queryByLabelText("New secret")).not.toBeInTheDocument();
    expect(within(form).getByText("Stored header names: X-Team")).toBeInTheDocument();
    // The header rows are not offered until the headers are replaced as a whole.
    expect(within(form).queryByLabelText("Header 1 name")).not.toBeInTheDocument();
    for (const input of within(form).queryAllByRole("textbox")) {
      expect((input as HTMLInputElement).value).not.toContain("soar.example");
    }
  });

  it("an untouched edit sends only the name and flags, never the URL, secret or headers", async () => {
    api.saveDestination.mockResolvedValue(destination());
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Edit destination SOAR" }));
    await user.click(screen.getByRole("button", { name: "Save destination" }));
    await waitFor(() => expect(api.saveDestination).toHaveBeenCalledTimes(1));
    const [id, body] = api.saveDestination.mock.calls[0];
    expect(id).toBe("d-1");
    expect(body).toEqual({ name: "SOAR", enabled: true, allow_http: false, allow_private: false });
  });

  it("replaces the URL and the secret only on request and clears the secret with null", async () => {
    api.saveDestination.mockResolvedValue(destination());
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Edit destination SOAR" }));
    await user.type(screen.getByLabelText("Replace URL"), "https://new.example/hook");
    await user.click(screen.getByRole("radio", { name: "Remove the secret" }));
    await user.click(screen.getByRole("button", { name: "Save destination" }));
    await waitFor(() => expect(api.saveDestination).toHaveBeenCalled());
    expect(api.saveDestination.mock.calls[0][1]).toMatchObject({
      url: "https://new.example/hook",
      hmac_secret: null,
    });
    expect(api.saveDestination.mock.calls[0][1]).not.toHaveProperty("headers");
  });

  it("replacing the headers opens name and value rows with write-only values", async () => {
    api.saveDestination.mockResolvedValue(destination());
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Edit destination SOAR" }));
    await user.click(screen.getByRole("radio", { name: "Replace all headers" }));
    const value = screen.getByLabelText("Header 1 value");
    expect(value).toHaveAttribute("type", "password");
    expect(value).toHaveValue("");
    await user.type(screen.getByLabelText("Header 1 name"), "X-Key");
    await user.type(value, "abc");
    await user.click(screen.getByRole("button", { name: "Save destination" }));
    await waitFor(() => expect(api.saveDestination).toHaveBeenCalled());
    expect(api.saveDestination.mock.calls[0][1]).toMatchObject({ headers: { "X-Key": "abc" } });
  });

  it("warns, in one sentence each, about HTTP and private addresses", async () => {
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Add destination" }));
    const form = screen.getByRole("form", { name: "Add a webhook destination" });
    const http = within(form).getByRole("checkbox", { name: "Allow HTTP" });
    expect(http).toHaveAccessibleDescription(/sent in clear text/);
    const priv = within(form).getByRole("checkbox", { name: "Allow private addresses" });
    expect(priv).toHaveAccessibleDescription(/internal or loopback address/);
    expect(http).not.toBeChecked();
    expect(priv).not.toBeChecked();
  });

  it("creates a destination, closes the form and re-reads without echoing what was typed", async () => {
    api.createDestination.mockResolvedValue(destination({ id: "d-2", name: "New" }));
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Add destination" }));
    const form = screen.getByRole("form", { name: "Add a webhook destination" });
    expect(within(form).getByRole("button", { name: "Add destination" })).toBeDisabled();
    await user.type(within(form).getByLabelText("Name"), "New");
    await user.type(within(form).getByLabelText("URL"), "https://new.example/secret-path?k=1");
    await user.type(within(form).getByLabelText("Sign with a secret"), "topsecret");
    await user.click(within(form).getByRole("button", { name: "Add destination" }));
    await waitFor(() => expect(api.createDestination).toHaveBeenCalled());
    expect(api.createDestination.mock.calls[0][0]).toMatchObject({
      name: "New",
      url: "https://new.example/secret-path?k=1",
      hmac_secret: "topsecret",
      headers: {},
    });
    await waitFor(() => expect(screen.queryByRole("form")).not.toBeInTheDocument());
    expect(document.body.textContent).not.toMatch(/secret-path|topsecret/);
    expect(api.destinations).toHaveBeenCalledTimes(2);
  });

  it("shows a server refusal in the form verbatim and keeps the draft", async () => {
    api.createDestination.mockRejectedValue(
      coded(
        422,
        "The destination resolves to a non-public address. Enable private addresses only for an endpoint the site administrator trusts.",
        "notifications.private_refused",
      ),
    );
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Add destination" }));
    await user.type(screen.getByLabelText("Name"), "Lab");
    await user.type(screen.getByLabelText("URL"), "https://10.0.0.5/x");
    await user.click(within(screen.getByRole("form")).getByRole("button", { name: "Add destination" }));
    const banner = await screen.findByText("The destination was not saved.");
    expect(banner.closest("[role=alert]")).toHaveTextContent(
      "The destination resolves to a non-public address. Enable private addresses only for an endpoint the site administrator trusts.",
    );
    expect(screen.getByLabelText("Name")).toHaveValue("Lab");
  });
});

describe("send test", () => {
  async function send() {
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Send a test webhook to SOAR" }));
  }

  it("says the destination accepted it", async () => {
    api.testDestination.mockResolvedValue(testResult());
    await send();
    const banner = (await screen.findByText("SOAR accepted the test.")).closest(".banner") as HTMLElement;
    expect(banner).toHaveClass("ok");
    expect(banner).toHaveTextContent("The destination accepted the test webhook.");
    expect(banner).toHaveTextContent("Result: ok 204.");
    expect(api.testDestination).toHaveBeenCalledWith("d-1");
    // The row is read again so the last test column shows the new result.
    await waitFor(() => expect(api.destinations).toHaveBeenCalledTimes(2));
  });

  it("names the class and status of an HTTP failure and what to do", async () => {
    api.testDestination.mockResolvedValue(
      testResult({
        ok: false,
        status: 503,
        result: "http_status",
        outcome: {
          code: "notifications.test_failed",
          params: { failure_class: "http_status" },
          message: "The test webhook was not accepted. Result: http_status.",
        },
      }),
    );
    await send();
    const banner = (await screen.findByText("SOAR did not accept the test.")).closest(".banner") as HTMLElement;
    expect(banner).toHaveClass("crit");
    expect(banner).toHaveTextContent("Result: http_status 503.");
    expect(banner).toHaveTextContent("Check its log.");
  });

  it("explains an SSRF refusal", async () => {
    api.testDestination.mockResolvedValue(
      testResult({
        ok: false,
        status: null,
        result: "ssrf_refused",
        outcome: {
          code: "notifications.test_failed",
          params: { failure_class: "ssrf_refused" },
          message: "The test webhook was not accepted. Result: ssrf_refused.",
        },
      }),
    );
    await send();
    const banner = (await screen.findByText("SOAR did not accept the test.")).closest(".banner") as HTMLElement;
    expect(banner).toHaveTextContent("Result: ssrf_refused.");
    expect(banner).toHaveTextContent("The address is private, or it uses HTTP without the flag.");
  });

  it("shows the rate-limit refusal and does not claim a result", async () => {
    api.testDestination.mockRejectedValue(new ApiError(429, "Too Many Requests", undefined, undefined, 30));
    await send();
    const banner = (await screen.findByText("The test of SOAR was not sent.")).closest("[role=alert]") as HTMLElement;
    expect(banner).toHaveTextContent(/30/);
    expect(screen.queryByText("SOAR accepted the test.")).not.toBeInTheDocument();
    expect(screen.queryByText("SOAR did not accept the test.")).not.toBeInTheDocument();
  });
});

describe("delete", () => {
  it("asks first, and a destination that a rule uses is refused with the server's sentence", async () => {
    api.deleteDestination.mockRejectedValue(
      coded(
        409,
        "The destination SOAR is used by a notification rule. Remove it from the rule first.",
        "notifications.destination_in_use",
        { name: "SOAR" },
      ),
    );
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Delete destination SOAR" }));
    expect(api.deleteDestination).not.toHaveBeenCalled();
    expect(screen.getByText("Delete the destination SOAR?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete the destination" }));
    const banner = (await screen.findByText(/is used by a notification rule/)).closest("[role=alert]");
    expect(banner).toHaveTextContent(
      "The destination SOAR is used by a notification rule. Remove it from the rule first.",
    );
    expect(screen.getByText("Delete the destination SOAR?")).toBeInTheDocument();
    expect(api.destinations).toHaveBeenCalledTimes(1);
  });

  it("deletes and re-reads", async () => {
    api.deleteDestination.mockResolvedValue(undefined);
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Delete destination SOAR" }));
    await user.click(screen.getByRole("button", { name: "Delete the destination" }));
    await waitFor(() => expect(api.deleteDestination).toHaveBeenCalledWith("d-1"));
    await waitFor(() => expect(api.destinations).toHaveBeenCalledTimes(2));
  });
});

describe("rules table", () => {
  it("states when each rule applies, in UTC, and where it goes", async () => {
    api.rules.mockResolvedValue([
      rule({
        families: ["waf", "ips"],
        owners: ["web-team"],
        source_classes: ["WAF"],
        group_ids: [12],
        window_start_minute: 1320,
        window_end_minute: 360,
        stop_default: true,
      }),
    ]);
    await loaded();
    const row = screen.getByText("Critical to SOAR").closest("tr") as HTMLElement;
    expect(row).toHaveTextContent("High and above");
    expect(row).toHaveTextContent("Families: WAF, IPS");
    expect(row).toHaveTextContent("Owners: web-team");
    expect(row).toHaveTextContent("Classes: WAF");
    expect(row).toHaveTextContent("Groups: 12");
    expect(row).toHaveTextContent("22:00 to 06:00 UTC, across midnight");
    expect(within(row).getByText("SOAR")).toBeInTheDocument();
    expect(within(row).getByText("Stops .env/e-mail routes")).toHaveClass("warn");
  });

  it("says a rule without filters matches every alert and a disabled one is disabled", async () => {
    api.rules.mockResolvedValue([
      rule({ families: null, enabled: false, stop_default: false }),
    ]);
    await loaded();
    const row = screen.getByText("Critical to SOAR").closest("tr") as HTMLElement;
    expect(within(row).getByText("Disabled")).toBeInTheDocument();
    expect(within(row).queryByText("Stops .env/e-mail routes")).not.toBeInTheDocument();
    expect(row).not.toHaveTextContent("Families:");
  });

  it("disables adding at fifty rules and says so", async () => {
    api.rules.mockResolvedValue(
      Array.from({ length: 50 }, (_, i) => rule({ id: `r-${i}`, name: `Rule ${i}` })),
    );
    await loaded();
    expect(screen.getByRole("button", { name: "Add rule" })).toBeDisabled();
    expect(screen.getByText(/maximum of 50 rules/)).toBeInTheDocument();
  });

  it("enables and disables a rule with a PATCH of that one field", async () => {
    api.saveRule.mockResolvedValue(rule({ enabled: false }));
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Disable rule Critical to SOAR" }));
    await waitFor(() => expect(api.saveRule).toHaveBeenCalledWith("r-1", { enabled: false }));
  });

  it("deletes a rule after asking", async () => {
    api.deleteRule.mockResolvedValue(undefined);
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Delete rule Critical to SOAR" }));
    expect(api.deleteRule).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Delete the rule" }));
    await waitFor(() => expect(api.deleteRule).toHaveBeenCalledWith("r-1"));
  });
});

describe("rule form", () => {
  async function open() {
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Add rule" }));
    return { user, form: screen.getByRole("form", { name: "Add a routing rule" }) };
  }

  it("offers every destination, marks a disabled one, and needs at least one pick", async () => {
    api.destinations.mockResolvedValue([
      destination(),
      destination({ id: "d-2", name: "Chat", enabled: false }),
    ]);
    const { user, form } = await open();
    const save = within(form).getByRole("button", { name: "Add rule" });
    await user.type(within(form).getByLabelText("Name"), "R1");
    expect(within(form).getByText("Pick at least one destination.")).toBeInTheDocument();
    expect(save).toBeDisabled();
    expect(within(form).getByRole("checkbox", { name: "Chat (disabled)" })).toBeInTheDocument();
    await user.click(within(form).getByRole("checkbox", { name: "SOAR" }));
    expect(save).toBeEnabled();
  });

  it("labels the window UTC, takes 24-hour times and explains a window across midnight", async () => {
    const { user, form } = await open();
    await user.click(within(form).getByRole("checkbox", { name: "Only inside a time window" }));
    expect(within(form).getAllByText("UTC").length).toBeGreaterThan(0);
    await user.type(within(form).getByLabelText("From"), "22:00");
    await user.type(within(form).getByLabelText("To"), "06:00");
    expect(
      within(form).getByText(
        "This window runs past midnight: from 22:00 UTC to 24:00, and on to 06:00 UTC the next day.",
      ),
    ).toBeInTheDocument();
  });

  it("does not claim a wrap for a window inside one day", async () => {
    const { user, form } = await open();
    await user.click(within(form).getByRole("checkbox", { name: "Only inside a time window" }));
    await user.type(within(form).getByLabelText("From"), "08:00");
    await user.type(within(form).getByLabelText("To"), "18:00");
    expect(within(form).queryByText(/runs past midnight/)).not.toBeInTheDocument();
  });

  it("refuses an invalid window before sending", async () => {
    const { user, form } = await open();
    await user.type(within(form).getByLabelText("Name"), "R1");
    await user.click(within(form).getByRole("checkbox", { name: "SOAR" }));
    await user.click(within(form).getByRole("checkbox", { name: "Only inside a time window" }));
    await user.type(within(form).getByLabelText("From"), "22:00");
    await user.type(within(form).getByLabelText("To"), "22:00");
    await user.click(within(form).getByRole("button", { name: "Add rule" }));
    expect(
      await within(form).findByText(/Enter both times as HH:MM between 00:00 and 23:59/),
    ).toBeInTheDocument();
    expect(api.createRule).not.toHaveBeenCalled();
  });

  it("sends the window as UTC minutes, the picked destinations and stop_default", async () => {
    api.createRule.mockResolvedValue(rule());
    const { user, form } = await open();
    await user.type(within(form).getByLabelText("Name"), "Night");
    await user.click(within(form).getByRole("checkbox", { name: "SOAR" }));
    await user.click(within(form).getByRole("checkbox", { name: "WAF" }));
    await user.click(within(form).getByRole("checkbox", { name: "Only inside a time window" }));
    await user.type(within(form).getByLabelText("From"), "22:00");
    await user.type(within(form).getByLabelText("To"), "06:00");
    const stop = within(form).getByRole("checkbox", { name: /Stop the \.env channels/ });
    expect(stop).toHaveAccessibleDescription(/reach only the destinations picked here/);
    await user.click(stop);
    await user.click(within(form).getByRole("button", { name: "Add rule" }));
    await waitFor(() => expect(api.createRule).toHaveBeenCalled());
    expect(api.createRule.mock.calls[0][0]).toMatchObject({
      name: "Night",
      min_severity: "HIGH",
      families: ["waf"],
      window_start_minute: 1320,
      window_end_minute: 360,
      destination_ids: ["d-1"],
      stop_default: true,
    });
    await waitFor(() => expect(screen.queryByRole("form")).not.toBeInTheDocument());
  });

  it("shows the server's refusal verbatim and keeps the form open", async () => {
    api.createRule.mockRejectedValue(
      coded(422, "This site already has the maximum of 50 rules.", "notifications.rule_limit"),
    );
    const { user, form } = await open();
    await user.type(within(form).getByLabelText("Name"), "R1");
    await user.click(within(form).getByRole("checkbox", { name: "SOAR" }));
    await user.click(within(form).getByRole("button", { name: "Add rule" }));
    const banner = (await screen.findByText("The rule was not saved.")).closest("[role=alert]");
    expect(banner).toHaveTextContent("This site already has the maximum of 50 rules.");
    expect(screen.getByRole("form", { name: "Add a routing rule" })).toBeInTheDocument();
  });

  it("fills an edit with the stored rule and PATCHes cleared filters as null", async () => {
    api.rules.mockResolvedValue([rule({ families: ["waf"], owners: ["a"] })]);
    api.saveRule.mockResolvedValue(rule());
    const user = userEvent.setup();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Edit rule Critical to SOAR" }));
    const form = screen.getByRole("form", { name: "Edit a routing rule" });
    expect(within(form).getByLabelText("Name")).toHaveValue("Critical to SOAR");
    expect(within(form).getByLabelText("Owners")).toHaveValue("a");
    await user.clear(within(form).getByLabelText("Owners"));
    await user.click(within(form).getByRole("checkbox", { name: "WAF" }));
    await user.click(within(form).getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(api.saveRule).toHaveBeenCalled());
    expect(api.saveRule.mock.calls[0][1]).toMatchObject({ owners: null, families: null });
  });
});

describe("secrets never reach the page", () => {
  it("renders none of the write-only values from a fully populated list", async () => {
    await loaded();
    expect(document.body.textContent).not.toMatch(/hmac|secret_set|Bearer|password/i);
    expect(document.body.textContent).not.toMatch(/real-time|\blive\b/i);
  });
});
