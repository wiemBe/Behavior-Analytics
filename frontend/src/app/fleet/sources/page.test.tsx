import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FleetSourcesPage from "./page";
import { ApiError, fleetApi, type FleetSourcePage } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

const empty: FleetSourcePage = {
  items: [],
  total: 0,
  limit: 50,
  offset: 0,
  facets: { source_class: [], type: [], group: [], flow: [], profile: [] },
};

async function renderPage(params: Record<string, string | string[]> = {}) {
  return render(await FleetSourcesPage({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(fleetApi, "syncStatus").mockResolvedValue({ instances: [], running: false });
});

describe("FleetSourcesPage", () => {
  it("reads the list for the URL's query and shows only a title", async () => {
    const spy = vi.spyOn(fleetApi, "sourceList").mockResolvedValue(empty);
    await renderPage({ q: "fw", class: ["WAF", "IPS"], type_id: "12", sort: "eps", order: "desc", limit: "100" });
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({
        q: "fw",
        class: ["WAF", "IPS"],
        type_id: [12],
        sort: "eps",
        order: "desc",
        limit: 100,
        offset: 0,
      }),
    );
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(document.querySelector(".page-desc")).toBeNull();
    expect(screen.queryByText(/Times are UTC/)).toBeNull();
  });

  it("drops a value that is not on the allow-list before it reaches the API", async () => {
    const spy = vi.spyOn(fleetApi, "sourceList").mockResolvedValue(empty);
    await renderPage({ class: "DROP TABLE", flow: "nope", limit: "999999" });
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ class: undefined, flow: undefined, limit: 50 }),
    );
  });

  it("explains an empty inventory and an empty filtered result", async () => {
    vi.spyOn(fleetApi, "sourceList").mockResolvedValue(empty);
    const { unmount } = await renderPage();
    expect(screen.getByText("No log sources are in the inventory.")).toBeInTheDocument();
    unmount();
    await renderPage({ flow: "SILENT" });
    expect(screen.getByText("No source matches these filters.")).toBeInTheDocument();
  });

  it.each([
    [new ApiError(403, "no"), /read:fleet/],
    [new ApiError(401, "no"), /session has ended/i],
    [new ApiError(500, "x"), /could not be loaded/],
    [new Error("down"), /could not be loaded/],
  ])("says what failed on %s", async (err, text) => {
    vi.spyOn(fleetApi, "sourceList").mockRejectedValue(err);
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent(text);
  });
});
