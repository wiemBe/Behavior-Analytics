import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SearchCadencePage from "./page";
import Loading from "./loading";
import { ApiError, api, type SearchCadenceItem } from "@/lib/api";

function item(over: Partial<SearchCadenceItem> = {}): SearchCadenceItem {
  return {
    family: "PHASE_A_VOLUME", cadence_seconds: 3600,
    allowed_cadences_seconds: [300, 900, 1800, 3600, 7200, 14400, 28800],
    bucket_seconds: 300, settle_seconds: 120, last_slot_start: null,
    next_dispatch_at: "2026-10-12T10:00:00Z", updated_at: "2026-10-12T08:00:00Z",
    updated_by: null, enabled: true, last_run: null, ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("search cadence page", () => {
  it("has exactly one h1 and names the question in its description", async () => {
    vi.spyOn(api, "searchCadence").mockResolvedValue([item()]);
    render(await SearchCadencePage());
    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("Search cadence");
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("keeps the API's order", async () => {
    vi.spyOn(api, "searchCadence").mockResolvedValue([
      item({ family: "RULE_MATCH" }),
      item({ family: "PHASE_A_VOLUME" }),
    ]);
    render(await SearchCadencePage());
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("Rule matches");
    expect(rows[1]).toHaveTextContent("Phase A volume");
  });

  it("explains a whole-list failure and what to do, without a table", async () => {
    vi.spyOn(api, "searchCadence").mockRejectedValue(new ApiError(500, "boom"));
    render(await SearchCadencePage());
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/could not be shown/i);
    expect(alert).toHaveTextContent(/reload/i);
    expect(alert).not.toHaveTextContent("boom");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("names the read permission on a 403", async () => {
    vi.spyOn(api, "searchCadence").mockRejectedValue(new ApiError(403, "forbidden"));
    render(await SearchCadencePage());
    expect(screen.getByRole("alert")).toHaveTextContent("read:search_cadence");
  });

  it("treats an empty list as an error that names the seed problem", async () => {
    vi.spyOn(api, "searchCadence").mockResolvedValue([]);
    render(await SearchCadencePage());
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/no search families/i);
    expect(alert).toHaveTextContent(/seed/i);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("has a loading state that names what is loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading search cadence/i);
  });
});
