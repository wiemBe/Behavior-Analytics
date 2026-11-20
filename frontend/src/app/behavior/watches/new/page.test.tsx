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
  it("renders the editor for a writer and no latency statement", async () => {
    await renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "New property watch" })).toBeInTheDocument();
    expect(screen.getByLabelText("property-watch editor")).toBeInTheDocument();
    expect(screen.queryByText(/Watches are judged on collected 5-minute buckets/)).toBeNull();
  });

  it("states no latency figure even when the catalog has a cadence", async () => {
    vi.spyOn(api, "propertyWatchCatalog").mockResolvedValue(
      catalog({ cadence: { family: "PROPERTY_WATCH", cadence_seconds: 900, bucket_seconds: 300, settle_seconds: 120 } }),
    );
    await renderPage();
    expect(screen.queryByText(/Watches are judged on collected 5-minute buckets/)).toBeNull();
    expect(screen.queryByText(/22 min after the events arrive/)).toBeNull();
  });

  it("links Search cadence to no caller (the Guide holds the sentence)", async () => {
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
    expect(screen.queryByRole("link", { name: "Search cadence" })).toBeNull();
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
    expect(screen.getByRole("alert")).toHaveTextContent("Permissions unreadable");
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
    expect(screen.getByRole("alert")).toHaveTextContent("The catalog is unavailable");
    expect(screen.queryByText("secret")).toBeNull();
  });
});
