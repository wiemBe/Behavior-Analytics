import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DirectoryPage, buildDirectorySave, formFrom } from "./DirectoryPage";
import Loading from "./loading";
import DirectoryError from "./error";
import styles from "@/components/auth/auth.module.css";
import type { DirectoryConfig, DirectorySave } from "@/components/auth/client";
import { ApiError } from "@/lib/api";

const api = vi.hoisted(() => ({
  getDirectory: vi.fn(),
  saveDirectory: vi.fn(),
  testDirectory: vi.fn(),
}));
vi.mock("@/components/auth/client", () => api);

function config(over: Partial<DirectoryConfig> = {}): DirectoryConfig {
  return {
    enabled: true,
    urls: ["ldaps://dc1.example", "ldaps://dc2.example"],
    start_tls: false,
    ca_pem: null,
    bind_template: "cn={username},dc=example",
    timeout_seconds: 5,
    user_search_base: "ou=people,dc=example",
    version: 3,
    updated_by: "ada",
    updated_at: "2026-10-15T12:00:00Z",
    ...over,
  };
}

function emptyForm() {
  return formFrom(
    config({
      enabled: false,
      urls: [],
      start_tls: false,
      ca_pem: null,
      bind_template: "{username}",
      timeout_seconds: 5,
      user_search_base: null,
      version: 0,
      updated_by: null,
      updated_at: null,
    }),
  );
}

beforeEach(() => {
  api.getDirectory.mockReset();
  api.saveDirectory.mockReset();
  api.testDirectory.mockReset();
  api.getDirectory.mockResolvedValue(config());
});

describe("buildDirectorySave", () => {
  it("drops empty URLs and sends an empty timeout as null, not zero", () => {
    const form = emptyForm();
    form.urls = [" ldaps://dc1.example ", " "];
    form.timeout = "  ";
    form.caPem = "  ";
    form.searchBase = "  ";
    form.bindTemplate = "cn={username}";
    const body: DirectorySave = buildDirectorySave(form);
    expect(body.urls).toEqual(["ldaps://dc1.example"]);
    expect(body.timeout_seconds).toBeNull();
    expect(body.ca_pem).toBeNull();
    expect(body.user_search_base).toBeNull();
    expect(body.version).toBe(0);
  });

  it("rejects a timeout that is not a number by sending null", () => {
    const form = emptyForm();
    form.timeout = "abc";
    expect(buildDirectorySave(form).timeout_seconds).toBeNull();
    form.timeout = "8";
    expect(buildDirectorySave(form).timeout_seconds).toBe(8);
  });
});

describe("DirectoryPage", () => {
  it("says when nothing is saved yet", async () => {
    api.getDirectory.mockResolvedValue(
      config({
        enabled: false,
        urls: [],
        bind_template: "{username}",
        user_search_base: null,
        version: 0,
        updated_by: null,
        updated_at: null,
      }),
    );
    render(<DirectoryPage />);
    expect(await screen.findByText("No directory configuration is saved yet.")).toBeInTheDocument();
  });

  it("shows a saved configuration", async () => {
    render(<DirectoryPage />);
    const saved = await screen.findByText(/Version 3/);
    expect(saved).toHaveTextContent("Version 3, last saved 2026-10-15 12:00:00 UTC by ada");
    expect(saved.querySelector("time")).toHaveAttribute("datetime", "2026-10-15T12:00:00Z");
    const form = screen.getByRole("form", { name: "Directory configuration" });
    expect(within(form).getByLabelText("URL 1")).toHaveValue("ldaps://dc1.example");
    expect(within(form).getByLabelText("URL 2")).toHaveValue("ldaps://dc2.example");
    expect(within(form).getByLabelText("Directory sign-in enabled")).toBeChecked();
    expect(within(form).getByLabelText("User search base")).toHaveValue("ou=people,dc=example");
  });

  it("previews the bind name for a typed username", async () => {
    const user = userEvent.setup();
    render(<DirectoryPage />);
    const form = await screen.findByRole("form", { name: "Directory configuration" });
    await user.type(within(form).getByLabelText("Preview username"), "a*b");
    expect(form).toHaveTextContent("Bind name: cn=a\\2ab,dc=example");
    expect(within(screen.getByRole("form", { name: "Test connection" })).getByLabelText("Test username")).toHaveValue(
      "",
    );
  });

  it("keeps the typed form on 409 and reloads on request", async () => {
    const text = "The directory configuration is stale. Reload it and retry.";
    api.saveDirectory.mockRejectedValue(new ApiError(409, text, text));
    const user = userEvent.setup();
    render(<DirectoryPage />);
    const form = await screen.findByRole("form", { name: "Directory configuration" });
    const url = within(form).getByLabelText("URL 1");
    await user.clear(url);
    await user.type(url, "ldaps://typed.example");
    await user.click(within(form).getByRole("button", { name: "Save directory" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Someone else changed the directory configuration.");
    expect(alert).toHaveTextContent(text);
    expect(url).toHaveValue("ldaps://typed.example");
    api.getDirectory.mockResolvedValue(config({ urls: ["ldaps://reloaded.example"] }));
    await user.click(screen.getByRole("button", { name: "Reload" }));
    expect(await screen.findByText("Reloaded the directory configuration.")).toBeInTheDocument();
    expect(screen.getByLabelText("URL 1")).toHaveValue("ldaps://reloaded.example");
  });

  it("lists every 422 reason and keeps the form", async () => {
    api.saveDirectory.mockRejectedValue(
      new ApiError(422, "Unprocessable", ["URL 1 is not an LDAP URL", "timeout is required"]),
    );
    const user = userEvent.setup();
    render(<DirectoryPage />);
    const form = await screen.findByRole("form", { name: "Directory configuration" });
    await user.clear(within(form).getByLabelText("Timeout (seconds)"));
    await user.click(within(form).getByRole("button", { name: "Save directory" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The directory configuration was not saved.");
    expect(alert).toHaveTextContent("URL 1 is not an LDAP URL");
    expect(alert).toHaveTextContent("timeout is required");
    expect(within(form).getByLabelText("Timeout (seconds)")).toHaveValue("");
  });

  it("sends an empty timeout as null", async () => {
    api.saveDirectory.mockResolvedValue(config({ version: 4, timeout_seconds: 5 }));
    const user = userEvent.setup();
    render(<DirectoryPage />);
    const form = await screen.findByRole("form", { name: "Directory configuration" });
    await user.clear(within(form).getByLabelText("Timeout (seconds)"));
    await user.click(within(form).getByRole("button", { name: "Save directory" }));
    expect(api.saveDirectory).toHaveBeenCalledWith(expect.objectContaining({ timeout_seconds: null, version: 3 }));
  });

  it("shows a failed TLS test and clears the password", async () => {
    api.testDirectory.mockResolvedValue({
      tls: { status: "failed", reason: "certificate verify failed", trust: "system" },
      certificate: null,
      bind: { status: "not_attempted", reason: "TLS failed" },
      latency_ms: 12,
    });
    const user = userEvent.setup();
    render(<DirectoryPage />);
    const test = await screen.findByRole("form", { name: "Test connection" });
    await user.type(within(test).getByLabelText("Test username"), "ada");
    await user.type(within(test).getByLabelText("Test password"), "Test-Secret-99");
    await user.click(within(test).getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Connection failed at TLS.")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toHaveClass("pill", "crit");
    expect(screen.getByText(/certificate verify failed/)).toBeInTheDocument();
    expect(screen.getByText("Not attempted")).toHaveClass("pill");
    expect(screen.getByText("Not attempted")).not.toHaveClass("ok");
    expect(screen.getByText(/TLS failed/)).toBeInTheDocument();
    expect(screen.getByText("System trust store")).toBeInTheDocument();
    expect(screen.queryByText("failed")).not.toBeInTheDocument();
    expect(screen.queryByText("not_attempted")).not.toBeInTheDocument();
    expect(screen.queryByText("system")).not.toBeInTheDocument();
    expect(screen.getByText("No certificate was returned.")).toBeInTheDocument();
    expect(screen.getByText("12 ms")).toBeInTheDocument();
    expect(within(test).getByLabelText("Test password")).toHaveValue("");
    expect(within(test).getByLabelText("Test username")).toHaveValue("");
    expect(document.body.textContent).not.toContain("Test-Secret-99");
  });

  it("shows TLS success with invalid bind credentials", async () => {
    api.testDirectory.mockResolvedValue({
      tls: { status: "ok", reason: null, trust: "ca_pem" },
      certificate: { subject: "CN=dc1", issuer: "CN=lab-ca", not_after: "2027-01-02T03:04:05Z" },
      bind: { status: "invalid_credentials", reason: "invalid credentials" },
      latency_ms: 40,
    });
    const user = userEvent.setup();
    render(<DirectoryPage />);
    const test = await screen.findByRole("form", { name: "Test connection" });
    await user.type(within(test).getByLabelText("Test username"), "ada");
    await user.type(within(test).getByLabelText("Test password"), "Test-Secret-99");
    await user.click(within(test).getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("TLS succeeded. The bind did not.")).toBeInTheDocument();
    expect(screen.getByText("Refused")).toHaveClass("pill", "crit");
    expect(screen.getByText(/invalid credentials/)).toBeInTheDocument();
    expect(screen.getByText("OK")).toHaveClass("pill", "ok");
    expect(screen.getByText("CN=dc1")).toBeInTheDocument();
    expect(screen.getByText("CN=lab-ca")).toBeInTheDocument();
    expect(screen.getByText("2027-01-02 03:04:05 UTC")).toBeInTheDocument();
    expect(screen.getByText("Uploaded CA certificate")).toBeInTheDocument();
    expect(screen.queryByText("ca_pem")).not.toBeInTheDocument();
    expect(screen.getByText("40 ms")).toBeInTheDocument();
    expect(within(test).getByLabelText("Test password")).toHaveValue("");
  });

  it("shows a successful test and an unparseable expiry as sent", async () => {
    api.testDirectory.mockResolvedValueOnce({
      tls: { status: "ok", reason: null, trust: "system" },
      certificate: { subject: "CN=dc1", issuer: "CN=lab-ca", not_after: "2027-01-02T03:04:05Z" },
      bind: { status: "ok", reason: null },
      latency_ms: 7,
    });
    const user = userEvent.setup();
    render(<DirectoryPage />);
    const test = await screen.findByRole("form", { name: "Test connection" });
    await user.type(within(test).getByLabelText("Test username"), "ada");
    await user.type(within(test).getByLabelText("Test password"), "Test-Secret-99");
    await user.click(within(test).getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Connected and bound as ada.")).toBeInTheDocument();
    expect(screen.getAllByText("OK")).toHaveLength(2);
    expect(screen.getByText("System trust store")).toBeInTheDocument();
    expect(await screen.findByText("7 ms")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("Test-Secret-99");

    api.testDirectory.mockResolvedValueOnce({
      tls: { status: "ok", reason: null, trust: "system" },
      certificate: { subject: "CN=dc1", issuer: "CN=lab-ca", not_after: "not-a-date" },
      bind: { status: "ok", reason: null },
      latency_ms: 8,
    });
    await user.type(within(test).getByLabelText("Test username"), "ada");
    await user.type(within(test).getByLabelText("Test password"), "Test-Secret-99");
    await user.click(within(test).getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("not-a-date")).toBeInTheDocument();
  });

  it("clears the password when the test request itself fails", async () => {
    api.testDirectory.mockRejectedValue(new ApiError(422, "Unprocessable", ["the test needs a directory URL"]));
    const user = userEvent.setup();
    render(<DirectoryPage />);
    const test = await screen.findByRole("form", { name: "Test connection" });
    await user.type(within(test).getByLabelText("Test username"), "ada");
    await user.type(within(test).getByLabelText("Test password"), "Test-Secret-99");
    await user.click(within(test).getByRole("button", { name: "Test connection" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The connection test failed.");
    expect(alert).toHaveTextContent("the test needs a directory URL");
    expect(within(test).getByLabelText("Test password")).toHaveValue("");
  });

  it("keeps a short field at content width and describes it with the hint", async () => {
    render(<DirectoryPage />);
    const form = await screen.findByRole("form", { name: "Directory configuration" });
    const timeout = within(form).getByLabelText("Timeout (seconds)");
    const url = within(form).getByLabelText("URL 1");
    expect(timeout.className.split(" ")).toContain(styles.controlNum);
    expect(url.className.split(" ")).not.toContain(styles.controlNum);
    const base = within(form).getByLabelText("User search base");
    const hint = document.getElementById(base.getAttribute("aria-describedby") ?? "");
    expect(hint).not.toBeNull();
    expect(hint).toHaveTextContent("Optional. Empty sends no search base.");
    expect(hint?.className.split(" ")).toContain(styles.hint);
    expect(hint?.className.split(" ")).not.toContain(styles.sectionNote);
  });

  it("separates the test description from the first field", async () => {
    render(<DirectoryPage />);
    const note = await screen.findByText(/sent with the form above/);
    expect(note.className.split(" ")).toContain(styles.hint);
    expect(note.className.split(" ")).toContain(styles.sectionNote);
    // The page header leaves --sp-6 under its description. This note does the same.
    const css = readFileSync(resolve(__dirname, "../../../components/auth/auth.module.css"), "utf8");
    const block = /\.sectionNote\s*\{([^}]*)\}/.exec(css);
    expect(block, ".sectionNote").not.toBeNull();
    expect(block?.[1]).toMatch(/margin-bottom:\s*var\(--sp-6\)/);
  });

  it("says when the caller cannot change the directory", async () => {
    api.getDirectory.mockRejectedValue(new ApiError(403, "Missing permission: admin:auth", "Missing permission: admin:auth"));
    render(<DirectoryPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Your role cannot change the directory (permission admin:auth).");
    expect(screen.queryByRole("form", { name: "Directory configuration" })).not.toBeInTheDocument();
  });

  it("says when the configuration could not be loaded, and retries", async () => {
    api.getDirectory.mockRejectedValueOnce(new ApiError(500, "database down", "database down"));
    render(<DirectoryPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The directory configuration could not be loaded.",
    );
    api.getDirectory.mockResolvedValue(config({ version: 0, updated_at: null, updated_by: null, urls: [] }));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No directory configuration is saved yet.")).toBeInTheDocument();
  });
});

describe("directory route states", () => {
  it("names what is loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the directory configuration");
  });

  it("names the failure without the exception", async () => {
    const reset = vi.fn();
    render(
      <DirectoryError error={Object.assign(new Error("secret stack"), { digest: "ref-3" })} reset={reset} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Directory could not be displayed.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret stack");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
