import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import IpsSourcesPage from "./page";
import Loading from "./loading";
import { candidate, status } from "./fixtures";
import { ApiError, fleetApi, ipsApi, type FleetSource, type Page } from "@/lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(ipsApi, "candidates").mockResolvedValue([candidate()]);
  vi.spyOn(fleetApi, "sources").mockResolvedValue({
    items: [{ instance_id: "inst-1" } as FleetSource],
    total: 1,
    limit: 1,
    offset: 0,
  } as Page<FleetSource>);
});

function rowFor(name: string) {
  return screen.getByRole("row", { name: new RegExp(name) });
}

describe("IPS sources list", () => {
  it("has one h1 and links to each source's editor", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([status()]);
    render(await IpsSourcesPage());
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "CiscoFirepowerThreatDefense @ ftd-01" })).toHaveAttribute(
      "href",
      "/admin/ips-sources/ls-413",
    );
  });

  it("reads a valid, collecting source: type, version, validation, enabled, time", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([status()]);
    render(await IpsSourcesPage());
    const r = rowFor("CiscoFirepower");
    expect(within(r).getByText(/QRadar id 413 · Cisco Firepower Threat Defense/)).toBeInTheDocument();
    expect(within(r).getByText("v2")).toBeInTheDocument();
    expect(within(r).getByText("Valid (v2)")).toBeInTheDocument();
    expect(within(r).getByText("Enabled")).toBeInTheDocument();
    expect(within(r).getByText("Collecting")).toBeInTheDocument();
    expect(within(r).getByText("10-13 09:02 UTC")).toBeInTheDocument();
  });

  it("has a column for each fact the list answers with", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([status()]);
    render(await IpsSourcesPage());
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Source",
      "Profileversion",
      "Validation",
      "Enabled",
      "Collection",
      "Last validated (UTC)",
    ]);
  });

  it("shows a disabled source as disabled and paused with the reason", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([
      status({ log_source_name: "PaSeries @ pa-fw-01", enabled: false, collection_eligible: false, ineligible_reason: "the profile is disabled" }),
    ]);
    render(await IpsSourcesPage());
    const r = rowFor("PaSeries");
    expect(within(r).getByText("Disabled")).toBeInTheDocument();
    expect(within(r).getByText("Paused — the profile is disabled")).toBeInTheDocument();
  });

  it("shows INVALID and INCONCLUSIVE in words", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([
      status({ log_source_name: "bad", log_source_id: "b", profile_id: "pb", validation_status: "INVALID", collection_eligible: false }),
      status({ log_source_name: "quiet", log_source_id: "q", profile_id: "pq", validation_status: "INCONCLUSIVE", collection_eligible: false }),
    ]);
    render(await IpsSourcesPage());
    expect(within(rowFor("bad")).getByText("Invalid")).toBeInTheDocument();
    expect(within(rowFor("quiet")).getByText("Inconclusive")).toBeInTheDocument();
  });

  it("never breaks a status word inside, so another column folds instead", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([
      status({ log_source_name: "quiet", validation_status: "INCONCLUSIVE", collection_eligible: false }),
      status({ log_source_name: "pend", log_source_id: "p", profile_id: "pp", validation_pending: true }),
    ]);
    render(await IpsSourcesPage());
    expect(within(rowFor("quiet")).getByText("Inconclusive")).toHaveClass("ips-status");
    expect(within(rowFor("pend")).getByText(/^Validating v/)).toHaveClass("ips-status");
  });

  it("shows a pending validation", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([
      status({
        log_source_name: "pend", validation_pending: true, profile_version: 5,
        validation_status: "UNVALIDATED", validated_profile_version: null,
        collection_eligible: false, ineligible_reason: "profile version 5 has not been validated",
      }),
    ]);
    render(await IpsSourcesPage());
    expect(within(rowFor("pend")).getByText("Validating v5…")).toBeInTheDocument();
  });

  it("shows a stale VALID as not validated for the current version, with the version it checked", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([
      status({
        log_source_name: "moved", profile_version: 5, validation_status: "VALID",
        validated_profile_version: 4, collection_eligible: false,
        ineligible_reason: "profile version 5 has not been validated (last validated: 4)",
      }),
    ]);
    render(await IpsSourcesPage());
    const r = rowFor("moved");
    expect(within(r).getByText("Version 5 not validated")).toBeInTheDocument();
    expect(within(r).queryByText(/^Valid/)).not.toBeInTheDocument();
    expect(within(r).getByText("of v4")).toBeInTheDocument();
  });

  it("shows a never-validated source with a dash that says so, not a date", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([
      status({
        log_source_name: "fresh", profile_version: 1, validation_status: "UNVALIDATED",
        validated_profile_version: null, validated_at: null, collection_eligible: false, enabled: false,
      }),
    ]);
    render(await IpsSourcesPage());
    const r = rowFor("fresh");
    expect(within(r).getByText("Version 1 not validated")).toBeInTheDocument();
    expect(within(r).getByText(/never validated/)).toBeInTheDocument();
  });

  it("lists sources that need attention first", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([
      status({ log_source_name: "a-fine", log_source_id: "a", profile_id: "pa" }),
      status({ log_source_name: "z-bad", log_source_id: "z", profile_id: "pz", validation_status: "INVALID", collection_eligible: false }),
    ]);
    render(await IpsSourcesPage());
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("z-bad");
    expect(rows[1]).toHaveTextContent("a-fine");
  });

  it("offers to add a source, using the instance of the existing profiles", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([status({ instance_id: "inst-7" })]);
    render(await IpsSourcesPage());
    expect(screen.getByRole("button", { name: "Add IPS source" })).toBeInTheDocument();
    expect(fleetApi.sources).not.toHaveBeenCalled();
  });

  it("says what is empty and that adding starts from a preset", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([]);
    render(await IpsSourcesPage());
    expect(screen.getByText("No IPS source has a profile yet.")).toBeInTheDocument();
    expect(screen.getByText(/starts from a preset/)).toBeInTheDocument();
    expect(screen.getByText(/the profile still has to validate/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add IPS source" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("explains a failure, says nothing is shown, and does not leak the error", async () => {
    vi.spyOn(ipsApi, "sources").mockRejectedValue(new ApiError(500, "psycopg2 stack trace"));
    render(await IpsSourcesPage());
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/could not be shown/i);
    expect(alert).toHaveTextContent(/reload/i);
    expect(alert).not.toHaveTextContent("psycopg2");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText(/no ips source has a profile/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add IPS source" })).not.toBeInTheDocument();
  });

  it("names admin:ips on a 403, and says admin:waf does not grant it", async () => {
    vi.spyOn(ipsApi, "sources").mockRejectedValue(new ApiError(403, "forbidden"));
    render(await IpsSourcesPage());
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("admin:ips");
    expect(alert).toHaveTextContent("admin:waf does not grant it");
  });

  it("has a loading state that names what is loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading ips sources/i);
  });
});
