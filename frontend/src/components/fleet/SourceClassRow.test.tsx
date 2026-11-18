import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, fleetApi, type CallerInfo, type FleetSourceRow } from "@/lib/api";

import { SourceClassRow } from "./SourceClassRow";

function row(over: Partial<FleetSourceRow> = {}): FleetSourceRow {
  return {
    id: "s1",
    instance_id: "inst/1",
    qradar_id: 7,
    name: "dmz-waf-01",
    type_name: "F5",
    enabled: true,
    auto_discovered: true,
    first_seen_at: "2026-10-13T02:40:00Z",
    source_class: "FIREWALL",
    basis: "NAME_REGEX",
    rule_id: null,
    override_class: null,
    override_by: null,
    override_at: null,
    conflicts: [],
    classified_at: null,
    review_status: "NEW",
    reviewed_at: null,
    reviewed_by: null,
    identifier: null,
    type_id: 1,
    group_names: [],
    group_count: 0,
    flow: "FLOWING",
    last_event_at: null,
    silent_for_seconds: null,
    current_eps: null,
    expected_eps: null,
    expected_eps_low: null,
    expected_eps_high: null,
    eps_state: null,
    waf_profile: null,
    ips_profile: null,
    identity_profile: null,
    ...over,
  };
}

function session(permissions: string[]): SessionValue {
  const me: CallerInfo = { subject: "u", email: null, permissions, effective_permissions: permissions };
  return { status: "ready", me, error: null, reload: async () => {}, signOut: async () => null };
}

function view(initial: FleetSourceRow | null, perms = ["log_source:write"], failed = false) {
  return render(
    <StaticSession value={session(perms)}>
      <SourceClassRow id="s1" initial={initial} failed={failed} />
    </StaticSession>,
  );
}

beforeEach(() => vi.restoreAllMocks());

describe("reading", () => {
  it("says the class read failed in one line", () => {
    view(null, undefined, true);
    expect(screen.getByText("Class could not be read.")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("shows the class and its rule basis", () => {
    view(row());
    expect(screen.getByText("Firewall", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByText("Name rule")).toBeInTheDocument();
  });

  it("shows an override with who set it", () => {
    view(row({ override_class: "WAF", override_by: "efe", source_class: "WAF", basis: "OVERRIDE" }));
    expect(screen.getByText("override by efe")).toBeInTheDocument();
  });

  it("is read-only without log_source:write: no select, no button", () => {
    view(row(), ["read:fleet"]);
    expect(screen.getByText("Firewall", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });
});

describe("editing", () => {
  it("offers the assignable classes incl. WAF and IPS, and Clear override only with an override", () => {
    const { unmount } = view(row());
    const select = screen.getByRole("combobox", { name: "New class" });
    const names = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(names).toContain("WAF");
    expect(names).toContain("IPS");
    expect(names).not.toContain("Clear override");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    unmount();
    view(row({ override_class: "WAF", source_class: "WAF", basis: "OVERRIDE" }));
    const withOverride = within(screen.getByRole("combobox", { name: "New class" })).getAllByRole("option");
    expect(withOverride.map((o) => o.textContent)).toContain("Clear override");
  });

  it("saves only on Save and shows the response, never before it", async () => {
    let resolve!: (r: FleetSourceRow) => void;
    const spy = vi.spyOn(fleetApi, "setOverride").mockReturnValue(new Promise((r) => (resolve = r)));
    view(row());
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "New class" }), "WAF");
    expect(spy).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(spy).toHaveBeenCalledWith("s1", "WAF");
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(screen.queryByText("override")).toBeNull();
    resolve(row({ source_class: "WAF", basis: "OVERRIDE", override_class: "WAF", override_by: "u" }));
    expect(await screen.findByText("override by u")).toBeInTheDocument();
    expect(screen.getByText("WAF", { selector: ".pill" })).toBeInTheDocument();
  });

  it("clears the override with null", async () => {
    const spy = vi
      .spyOn(fleetApi, "setOverride")
      .mockResolvedValue(row({ source_class: "FIREWALL", basis: "NAME_REGEX", override_class: null }));
    view(row({ override_class: "WAF", source_class: "WAF", basis: "OVERRIDE", override_by: "u" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "New class" }), "CLEAR");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(spy).toHaveBeenCalledWith("s1", null);
    expect(await screen.findByText("Name rule")).toBeInTheDocument();
    expect(screen.queryByText(/override by/)).toBeNull();
  });

  it.each([
    [403, /log_source:write/],
    [404, /no longer exists/],
    [422, /refused/],
    [500, /backend could not be reached/],
  ])("says a %s in one inline line and keeps the old class", async (status, expected) => {
    vi.spyOn(fleetApi, "setOverride").mockRejectedValue(new ApiError(status, "x"));
    view(row());
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "New class" }), "IPS");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(expected);
    expect(screen.getByText("Firewall", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });
});

describe("profile link", () => {
  it("WAF without a profile links to create, carrying the instance", () => {
    view(row({ source_class: "WAF" }));
    expect(screen.getByRole("link", { name: "Create WAF profile" })).toHaveAttribute(
      "href",
      "/admin/waf-sources/s1?instance=inst%2F1",
    );
  });

  it("WAF with a profile links to it", () => {
    view(row({ source_class: "WAF", waf_profile: { status: "VALID", enabled: true } }));
    expect(screen.getByRole("link", { name: "WAF profile" })).toHaveAttribute("href", "/admin/waf-sources/s1");
    expect(screen.queryByRole("link", { name: /Create/ })).toBeNull();
  });

  it("IPS likewise", () => {
    const { unmount } = view(row({ source_class: "IPS" }));
    expect(screen.getByRole("link", { name: "Create IPS profile" })).toHaveAttribute(
      "href",
      "/admin/ips-sources/s1?instance=inst%2F1",
    );
    unmount();
    view(row({ source_class: "IPS", ips_profile: { status: "UNVALIDATED", enabled: false } }));
    expect(screen.getByRole("link", { name: "IPS profile" })).toHaveAttribute("href", "/admin/ips-sources/s1");
  });

  it("other classes have no link, and a link follows a changed class", async () => {
    vi.spyOn(fleetApi, "setOverride").mockResolvedValue(
      row({ source_class: "WAF", override_class: "WAF", basis: "OVERRIDE" }),
    );
    view(row());
    expect(screen.queryByRole("link")).toBeNull();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "New class" }), "WAF");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("link", { name: "Create WAF profile" })).toBeInTheDocument();
  });
});
