import { cleanup, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { catalog } from "@/components/propertywatch/fixtures";
import { ApiError, api } from "@/lib/api";

import NewPropertyWatchPage from "./page";

vi.mock("@/components/propertywatch/WatchEditor", () => ({
  WatchEditor: () => <div aria-label="property-watch editor" />,
}));

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "propertyWatchCatalog").mockResolvedValue(catalog());
  vi.spyOn(api, "caller").mockResolvedValue({
    subject: "engineer",
    email: null,
    permissions: ["property_watch:write"],
    effective_permissions: ["read:property_watches", "property_watch:write"],
  });
});

async function renderPage() {
  return render(await NewPropertyWatchPage());
}

describe("new property-watch route", () => {
  it("renders the editor for a writer and the latency statement once", async () => {
    await renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "New property watch" })).toBeInTheDocument();
    expect(screen.getByLabelText("property-watch editor")).toBeInTheDocument();
    expect(screen.getAllByText(/Watches are judged on collected 5-minute buckets, after each collection of their family/)).toHaveLength(1);
  });

  it("states the figure from the catalog's cadence, and the old words without one", async () => {
    vi.spyOn(api, "propertyWatchCatalog").mockResolvedValue(
      catalog({ cadence: { family: "PROPERTY_WATCH", cadence_seconds: 900, bucket_seconds: 300, settle_seconds: 120 } }),
    );
    await renderPage();
    expect(screen.getAllByText(/Watches are judged on collected 5-minute buckets, up to 22 min after the events arrive/)).toHaveLength(1);
    expect(screen.queryByText(/after each collection of their family/)).toBeNull();
  });

  it("links Search cadence only to a reader of read:search_cadence", async () => {
    await renderPage();
    expect(screen.queryByRole("link", { name: "Search cadence" })).toBeNull();
    cleanup();
    vi.spyOn(api, "caller").mockResolvedValue({
      subject: "engineer",
      email: null,
      permissions: ["property_watch:write", "read:*"],
      effective_permissions: ["read:property_watches", "property_watch:write", "read:search_cadence"],
    });
    await renderPage();
    expect(screen.getByRole("link", { name: "Search cadence" })).toHaveAttribute(
      "href",
      "/admin/search-cadence",
    );
  });

  it("hides the editor from a reader and names property_watch:write", async () => {
    vi.spyOn(api, "caller").mockResolvedValue({
      subject: "analyst",
      email: null,
      permissions: ["read:*"],
      effective_permissions: ["read:property_watches"],
    });
    await renderPage();
    expect(screen.queryByLabelText("property-watch editor")).toBeNull();
    expect(screen.getByText("property_watch:write").tagName).toBe("CODE");
  });

  it("fails closed when permissions cannot be read", async () => {
    vi.spyOn(api, "caller").mockRejectedValue(new ApiError(500, "down"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("Permissions could not be read");
    expect(screen.queryByLabelText("property-watch editor")).toBeNull();
  });

  it("names the permission when the catalog is forbidden", async () => {
    vi.spyOn(api, "propertyWatchCatalog").mockRejectedValue(new ApiError(403, "Forbidden"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("read:property_watches");
  });

  it("does not expose a failed catalog response", async () => {
    vi.spyOn(api, "propertyWatchCatalog").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("catalog of switches and bounds is unavailable");
    expect(screen.queryByText("secret")).toBeNull();
  });
});
