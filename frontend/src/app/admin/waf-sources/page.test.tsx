import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WafSourcesPage from "./page";
import Loading from "./loading";
import { SCHEMA, status } from "@/components/fieldmapping/fixtures";
import { ApiError, api } from "@/lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "wafMappingSchema").mockResolvedValue(SCHEMA);
  vi.spyOn(api, "wafCandidates").mockResolvedValue([]);
});

function rowFor(name: string) {
  return screen.getByRole("row", { name: new RegExp(name) });
}

describe("WAF sources list", () => {
  it("has one h1 and links to each source's editor", async () => {
    vi.spyOn(api, "wafSources").mockResolvedValue([status()]);
    render(await WafSourcesPage());
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "WAF edge - eu-west" })).toHaveAttribute(
      "href",
      "/admin/waf-sources/ls-1",
    );
  });

  it("reads a valid, collecting source", async () => {
    vi.spyOn(api, "wafSources").mockResolvedValue([status()]);
    render(await WafSourcesPage());
    const r = rowFor("WAF edge");
    expect(within(r).getByText("Valid (v4)")).toBeInTheDocument();
    expect(within(r).getByText("Collecting")).toBeInTheDocument();
    expect(within(r).getByText("v4")).toBeInTheDocument();
    expect(within(r).getByText("10-12 23:05 UTC")).toBeInTheDocument();
  });

  it("names the property that failed validation", async () => {
    vi.spyOn(api, "wafSources").mockResolvedValue([
      status({
        log_source_name: "bad",
        validation_status: "INVALID",
        collection_eligible: false,
        ineligible_reason: "validation is INVALID",
        validation_fields: [{ field: "URL", property_name: "WAF_URI", status: "PROPERTY_REJECTED" }],
      }),
    ]);
    render(await WafSourcesPage());
    const r = rowFor("bad");
    expect(within(r).getByText("Invalid: URL path property does not exist")).toBeInTheDocument();
    expect(within(r).getByText("Paused — validation is INVALID")).toBeInTheDocument();
  });

  it("shows a pending validation", async () => {
    vi.spyOn(api, "wafSources").mockResolvedValue([
      status({
        log_source_name: "pend", validation_pending: true, profile_version: 5,
        validation_status: "UNVALIDATED", validated_profile_version: null,
        collection_eligible: false, ineligible_reason: "version 5 is not validated",
      }),
    ]);
    render(await WafSourcesPage());
    expect(within(rowFor("pend")).getByText("Validating v5…")).toBeInTheDocument();
  });

  it("shows a changed mapping as not validated, paused, and the older validation it has", async () => {
    vi.spyOn(api, "wafSources").mockResolvedValue([
      status({
        log_source_name: "moved", profile_version: 5, validation_status: "UNVALIDATED",
        validated_profile_version: 4, collection_eligible: false,
        ineligible_reason: "version 5 is not validated",
      }),
    ]);
    render(await WafSourcesPage());
    const r = rowFor("moved");
    expect(within(r).getByText("Version 5 not validated")).toBeInTheDocument();
    expect(within(r).getByText("Paused — version 5 is not validated")).toBeInTheDocument();
    expect(within(r).getByText("of v4")).toBeInTheDocument();
  });

  it("shows an inconclusive source with its reason", async () => {
    vi.spyOn(api, "wafSources").mockResolvedValue([
      status({
        log_source_name: "quiet", validation_status: "INCONCLUSIVE", collection_eligible: false,
        validation_fields: [{ field: "URL", property_name: "URL", status: "NO_EVENTS" }],
      }),
    ]);
    render(await WafSourcesPage());
    expect(within(rowFor("quiet")).getByText("Inconclusive: no events in the window")).toBeInTheDocument();
  });

  it("shows a never-validated source with a dash that says so, not a date", async () => {
    vi.spyOn(api, "wafSources").mockResolvedValue([
      status({
        log_source_name: "fresh", profile_version: 1, validation_status: "UNVALIDATED",
        validated_profile_version: null, validated_at: null, collection_eligible: false, enabled: false,
      }),
    ]);
    render(await WafSourcesPage());
    const r = rowFor("fresh");
    expect(within(r).getByText("Version 1 not validated")).toBeInTheDocument();
    expect(within(r).getByText(/never validated/)).toBeInTheDocument();
  });

  it("lists sources that need attention first", async () => {
    vi.spyOn(api, "wafSources").mockResolvedValue([
      status({ log_source_name: "a-fine", log_source_id: "a" , profile_id: "pa"}),
      status({ log_source_name: "z-bad", log_source_id: "z", profile_id: "pz", validation_status: "INVALID", collection_eligible: false }),
    ]);
    render(await WafSourcesPage());
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("z-bad");
    expect(rows[1]).toHaveTextContent("a-fine");
  });

  it("offers to add a source", async () => {
    vi.spyOn(api, "wafSources").mockResolvedValue([status()]);
    render(await WafSourcesPage());
    expect(screen.getByRole("button", { name: "Add WAF source" })).toBeInTheDocument();
  });

  it("still shows the table when only the schema failed", async () => {
    vi.spyOn(api, "wafSources").mockResolvedValue([status()]);
    vi.spyOn(api, "wafMappingSchema").mockRejectedValue(new ApiError(500, "boom"));
    render(await WafSourcesPage());
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("says what is empty and offers the Add action", async () => {
    vi.spyOn(api, "wafSources").mockResolvedValue([]);
    render(await WafSourcesPage());
    expect(screen.getByText("No log source is configured as a WAF source.")).toBeInTheDocument();
    expect(screen.getByText("A log source becomes one when its fields are mapped.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add WAF source" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("explains a failure, says nothing is shown, and does not leak the error", async () => {
    vi.spyOn(api, "wafSources").mockRejectedValue(new ApiError(500, "psycopg2 stack trace"));
    render(await WafSourcesPage());
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/could not be shown/i);
    expect(alert).toHaveTextContent(/reload/i);
    expect(alert).not.toHaveTextContent("psycopg2");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText(/no log source is configured/i)).not.toBeInTheDocument();
  });

  it("names the permission on a 403", async () => {
    vi.spyOn(api, "wafSources").mockRejectedValue(new ApiError(403, "forbidden"));
    render(await WafSourcesPage());
    expect(screen.getByRole("alert")).toHaveTextContent("admin:waf");
  });

  it("has a loading state that names what is loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading waf sources/i);
  });
});
