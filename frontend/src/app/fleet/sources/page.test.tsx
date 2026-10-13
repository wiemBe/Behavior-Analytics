import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FleetSourcesPage from "./page";
import { ApiError, fleetApi, type FleetSource } from "@/lib/api";

function src(over: Partial<FleetSource> = {}): FleetSource {
  return {
    id: "s1",
    instance_id: "i",
    qradar_id: 1,
    name: "alpha",
    type_name: "Snort",
    enabled: true,
    auto_discovered: false,
    first_seen_at: "2026-10-13T02:40:00Z",
    source_class: "IPS",
    basis: "TYPE_NAME_REGEX",
    rule_id: null,
    override_class: null,
    override_by: null,
    override_at: null,
    conflicts: [],
    classified_at: null,
    review_status: "REVIEWED",
    reviewed_at: null,
    reviewed_by: null,
    ...over,
  };
}

async function renderPage(params: Record<string, string> = {}) {
  return render(await FleetSourcesPage({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(fleetApi, "rules").mockResolvedValue([]);
});

describe("FleetSourcesPage", () => {
  it("passes the filters the API offers and shows them as chips", async () => {
    const spy = vi
      .spyOn(fleetApi, "sources")
      .mockResolvedValue({ items: [src()], total: 1, limit: 50, offset: 0 });
    await renderPage({ class: "IPS", review_status: "NEW", has_conflict: "true", basis: "NAME_REGEX" });
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ class: "IPS", review_status: "NEW", has_conflict: true, basis: "NAME_REGEX" }),
    );
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("list", { name: "Active filters" })).toHaveTextContent("Class: IPS");
    expect(screen.getByRole("combobox", { name: "Class" })).toHaveValue("IPS");
    expect(screen.getByRole("link", { name: "Clear" })).toBeInTheDocument();
  });

  it("ignores a filter value that is not on the allow-list", async () => {
    const spy = vi
      .spyOn(fleetApi, "sources")
      .mockResolvedValue({ items: [src()], total: 1, limit: 50, offset: 0 });
    await renderPage({ class: "DROP TABLE" });
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ class: undefined }));
  });

  it("explains an empty filtered result", async () => {
    vi.spyOn(fleetApi, "sources").mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    await renderPage({ review_status: "NEW" });
    expect(screen.getByText("No source matches these filters.")).toBeInTheDocument();
  });

  it("explains an empty inventory", async () => {
    vi.spyOn(fleetApi, "sources").mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    await renderPage();
    expect(screen.getByText("No log sources are in the inventory.")).toBeInTheDocument();
  });

  it("shows an error that says what to do", async () => {
    vi.spyOn(fleetApi, "sources").mockRejectedValue(new ApiError(500, "x"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent(/reload/i);
  });

  it("still lists sources when the rules cannot be read", async () => {
    vi.spyOn(fleetApi, "rules").mockRejectedValue(new Error("x"));
    vi.spyOn(fleetApi, "sources").mockResolvedValue({ items: [src()], total: 1, limit: 50, offset: 0 });
    await renderPage();
    expect(screen.getByText("alpha")).toBeInTheDocument();
  });
});
