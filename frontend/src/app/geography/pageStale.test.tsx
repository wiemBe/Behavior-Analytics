// The Geography page and the age of its answer (GEO-5): fresh, stale, null and missing.

import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { answer, notCollected } from "@/components/geo/fixtures";
import { geoApi } from "@/lib/api";

import GeographyPage from "./page";

vi.mock("echarts/core", () => ({
  init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), on: vi.fn() }),
  use: vi.fn(),
  registerMap: vi.fn(),
}));
vi.mock("echarts/charts", () => ({ MapChart: {} }));
vi.mock("echarts/components", () => ({ TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));
// No cadence read: the 2 h default applies, so stale past 4 h.
vi.mock("@/hooks/useApiResource", () => ({ useApiResource: () => ({ status: "loading" }) }));

const NOW = new Date("2026-11-02T12:00:00Z");
const at = (hoursAgo: number) => new Date(NOW.getTime() - hoursAgo * 3_600_000).toISOString();

async function renderPage() {
  return render(await GeographyPage({ searchParams: Promise.resolve({}) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

describe("the page's age line", () => {
  it("says where the data reaches when it is fresh", async () => {
    vi.spyOn(geoApi, "countries").mockResolvedValue(answer({ latest_bucket_end: at(1) }));
    await renderPage();
    expect(screen.getByText(/Data to/)).toHaveTextContent("Data to 11:00:00 UTC");
    expect(screen.queryByText(/Stale:/)).toBeNull();
  });

  it("marks a stale answer and keeps the table", async () => {
    vi.spyOn(geoApi, "countries").mockResolvedValue(answer({ latest_bucket_end: at(6) }));
    await renderPage();
    expect(screen.getByText(/Stale: the latest data is from/)).toHaveTextContent("6 h old");
    expect(screen.getByRole("table", { name: "Events by country" })).toBeInTheDocument();
  });

  it("makes no claim for a null newest bucket", async () => {
    vi.spyOn(geoApi, "countries").mockResolvedValue({ ...notCollected(), latest_bucket_end: null });
    await renderPage();
    expect(screen.getByText("Not collected.")).toBeInTheDocument();
    expect(screen.queryByText(/Stale:|Data to/)).toBeNull();
  });

  it("makes no claim when the backend does not send the field", async () => {
    const older = answer();
    delete older.latest_bucket_end;
    vi.spyOn(geoApi, "countries").mockResolvedValue(older);
    await renderPage();
    expect(screen.getByRole("table", { name: "Events by country" })).toBeInTheDocument();
    expect(screen.queryByText(/Stale:|Data to/)).toBeNull();
  });
});
