// The Geography widget: its frame states, the read it makes for each direction and range, and
// what it does with a stored direction it does not know. The body's own states (partial, lower
// bound, provenance) are in `components/geo/GeographyBody.test.tsx`.

import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";

import { answer, notCollected } from "@/components/geo/fixtures";
import { failed, forbidden, loading, ok } from "@/components/home/testkit";
import { WidgetRuntimeContext, type WidgetRuntime } from "@/widgets/runtime";

import { GeographyMap, GEO_WIDGET_ROWS } from "./GeographyMap";

const { setOption } = vi.hoisted(() => ({ setOption: vi.fn() }));
vi.mock("echarts/core", () => ({
  init: () => ({ setOption, resize: vi.fn(), dispose: vi.fn(), on: vi.fn() }),
  use: vi.fn(),
  registerMap: vi.fn(),
}));
vi.mock("echarts/charts", () => ({ MapChart: {} }));
vi.mock("echarts/components", () => ({ TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const families = vi.hoisted(() => [] as Array<string | null | undefined>);
vi.mock("@/widgets/cadence", async (orig) => {
  const actual = await orig<typeof import("@/widgets/cadence")>();
  return {
    ...actual,
    useFamilyTiming: (family: string | null | undefined) => {
      families.push(family);
      return actual.useFamilyTiming(family as never);
    },
  };
});

const res = vi.hoisted(() => ({ state: undefined as unknown, keys: [] as Array<string | null> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => {
    res.keys.push(key);
    return res.state ?? { status: "loading" };
  },
}));

afterEach(() => {
  vi.useRealTimers();
});

beforeEach(() => {
  res.state = undefined;
  res.keys = [];
  setOption.mockClear();
});

const link = () => screen.getByRole("link", { name: "Open the Geography page" });
const rows = () => within(screen.getByRole("table")).getAllByRole("row").slice(1);

function expandedRuntime(): WidgetRuntime {
  return {
    rangeOptions: null,
    override: null,
    dashboardRange: "24h",
    setRange: () => {},
    onExpand: null,
    expanded: true,
  };
}
const inRuntime = (node: ReactNode) => (
  <WidgetRuntimeContext.Provider value={expandedRuntime()}>{node}</WidgetRuntimeContext.Provider>
);

describe("the frame", () => {
  it("is polite loading text with the drill-down already there", () => {
    res.state = loading;
    render(<GeographyMap />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the country counts…");
    expect(link()).toHaveAttribute("href", "/geography?window=24h&direction=SOURCE");
  });

  it("says what failed and what to do, with no exception text, and keeps the link", () => {
    res.state = failed(500);
    render(<GeographyMap />);
    expect(screen.getByRole("alert")).toHaveTextContent("The country counts could not be read.");
    expect(screen.getByRole("alert")).toHaveTextContent("HTTP 500");
    expect(screen.getByRole("alert")).toHaveTextContent("Refresh");
    expect(link()).toBeInTheDocument();
  });

  it("names the permission when the role cannot read the counts", () => {
    res.state = forbidden;
    render(<GeographyMap />);
    expect(screen.getByRole("status")).toHaveTextContent("(permission read:geo)");
    expect(link()).toBeInTheDocument();
  });

  it("keeps the previous answer and says the refresh failed", () => {
    res.state = ok(answer(), { refreshFailed: { httpStatus: 503 } });
    render(<GeographyMap />);
    expect(rows()).toHaveLength(GEO_WIDGET_ROWS);
    expect(screen.getByText(/The latest refresh failed \(HTTP 503\)\./)).toBeInTheDocument();
  });

  it("titles the window with the direction and the range", () => {
    res.state = loading;
    render(<GeographyMap range="7d" config={{ direction: "DESTINATION" }} />);
    expect(screen.getByRole("heading", { name: /Geography/ })).toHaveTextContent(
      "destination addresses · 7 d",
    );
  });
});

describe("the answer", () => {
  it("lists the five largest countries and points to the page for the rest", () => {
    res.state = ok(answer());
    render(<GeographyMap />);
    expect(rows()).toHaveLength(5);
    expect(rows()[0]).toHaveTextContent("United States");
    expect(screen.getByText(/Top 5 of 18 countries/)).toBeInTheDocument();
  });

  it("lists every country when it is expanded", () => {
    res.state = ok(answer());
    render(inRuntime(<GeographyMap />));
    expect(rows()).toHaveLength(18);
  });

  it("says NOT_COLLECTED in the body, with no map and no empty table", () => {
    res.state = ok(notCollected());
    render(<GeographyMap />);
    expect(screen.getByText("Not collected.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
    expect(setOption).not.toHaveBeenCalled();
    expect(link()).toBeInTheDocument();
  });
});

describe("the read", () => {
  it("asks for the source addresses over 24 h when nothing is configured", () => {
    res.state = loading;
    render(<GeographyMap />);
    expect(res.keys.at(-1)).toBe("/geo/countries?window=24h&direction=SOURCE");
  });

  it.each(["SOURCE", "DESTINATION"] as const)("asks for the %s direction the config names", (direction) => {
    res.state = loading;
    render(<GeographyMap config={{ direction }} />);
    expect(res.keys.at(-1)).toBe(`/geo/countries?window=24h&direction=${direction}`);
    expect(link()).toHaveAttribute("href", `/geography?window=24h&direction=${direction}`);
  });

  it.each(["1h", "24h", "7d"] as const)("asks for the range %s", (range) => {
    res.state = loading;
    render(<GeographyMap range={range} />);
    expect(res.keys.at(-1)).toBe(`/geo/countries?window=${range}&direction=SOURCE`);
  });

  it("reads the source addresses for a direction it does not know, and says so", () => {
    res.state = ok(answer());
    render(<GeographyMap config={{ direction: "SIDEWAYS" as never }} />);
    expect(res.keys.at(-1)).toBe("/geo/countries?window=24h&direction=SOURCE");
    expect(screen.getByText(/The saved direction is not one this version knows/)).toBeInTheDocument();
  });

  it("says nothing about the direction when none is stored", () => {
    res.state = ok(answer());
    render(<GeographyMap config={{}} />);
    expect(screen.queryByText(/saved direction/)).toBeNull();
  });
});

describe("in Turkish", () => {
  beforeEach(() => setTestLocale("tr"));

  it("words the frame states in Turkish and names the permission", () => {
    res.state = forbidden;
    const { unmount } = render(<GeographyMap />);
    expect(screen.getByRole("status")).toHaveTextContent("read:geo");
    unmount();
    res.state = failed(500);
    render(<GeographyMap />);
    expect(screen.getByRole("alert")).toHaveTextContent("ülke sayımları");
    expect(screen.getByRole("link", { name: "Coğrafya sayfasını aç" })).toBeInTheDocument();
  });

  it("words the window and the not-collected state in Turkish", () => {
    res.state = ok(notCollected());
    render(<GeographyMap config={{ direction: "DESTINATION" }} />);
    expect(screen.getByRole("heading", { name: /Coğrafya/ })).toHaveTextContent("hedef adresler, 24 sa");
    expect(screen.getByText("Toplanmadı.")).toBeInTheDocument();
  });
});

// GEO-5: the answer's own data time is `latest_bucket_end`; the frame judges it against the
// GEO_COLLECTION cadence (2 h when the cadence is unknown, so stale past 4 h).
describe("the age of the data", () => {
  const NOW = new Date("2026-11-02T12:00:00Z");
  const at = (hoursAgo: number) => new Date(NOW.getTime() - hoursAgo * 3_600_000).toISOString();
  const withClock = () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  };

  it("shows where the data reaches, and no stale note, when the newest bucket is recent", () => {
    withClock();
    res.state = ok(answer({ latest_bucket_end: at(1) }));
    render(<GeographyMap />);
    expect(screen.getByText(/Data to/)).toHaveTextContent("Data to 11:00:00 UTC");
    expect(screen.queryByText(/Stale:/)).toBeNull();
  });

  it("marks the answer stale past twice the cadence, and still draws the map and the table", async () => {
    withClock();
    res.state = ok(answer({ latest_bucket_end: at(5) }));
    render(<GeographyMap />);
    expect(screen.getByText(/Stale: the latest data is from/)).toHaveTextContent(
      "Stale: the latest data is from 07:00:00 UTC, 5 h old.",
    );
    expect(rows()).toHaveLength(GEO_WIDGET_ROWS);
    expect(await screen.findByRole("img")).toBeInTheDocument();
    expect(link()).toBeInTheDocument();
  });

  it("judges the age by the GEO_COLLECTION cadence", () => {
    withClock();
    families.length = 0;
    res.state = ok(answer({ latest_bucket_end: at(1) }));
    render(<GeographyMap />);
    expect(new Set(families)).toEqual(new Set(["GEO_COLLECTION"]));
  });

  it("is not stale at exactly the threshold and stale just past it", () => {
    withClock();
    res.state = ok(answer({ latest_bucket_end: at(4) }));
    const { unmount } = render(<GeographyMap />);
    expect(screen.queryByText(/Stale:/)).toBeNull();
    unmount();
    res.state = ok(answer({ latest_bucket_end: new Date(NOW.getTime() - 4 * 3_600_000 - 1000).toISOString() }));
    render(<GeographyMap />);
    expect(screen.getByText(/Stale:/)).toBeInTheDocument();
  });

  it("makes no claim when the newest bucket is null (NOT_COLLECTED says it already)", () => {
    withClock();
    res.state = ok({ ...notCollected(), latest_bucket_end: null });
    render(<GeographyMap />);
    expect(screen.getByText("Not collected.")).toBeInTheDocument();
    expect(screen.queryByText(/Stale:|Data to/)).toBeNull();
  });

  it("makes no claim when an older backend does not send the field", () => {
    withClock();
    const older = answer();
    delete older.latest_bucket_end;
    res.state = ok(older);
    render(<GeographyMap />);
    expect(rows()).toHaveLength(GEO_WIDGET_ROWS);
    expect(screen.queryByText(/Stale:|Data to/)).toBeNull();
  });

  it("says it in Turkish too", () => {
    setTestLocale("tr");
    withClock();
    res.state = ok(answer({ latest_bucket_end: at(5) }));
    render(<GeographyMap />);
    expect(screen.getByText(/Güncel değil/)).toBeInTheDocument();
  });
});
