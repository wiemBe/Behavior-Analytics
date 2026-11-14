import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { setOption, init } = vi.hoisted(() => {
  const setOption = vi.fn();
  return { setOption, init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn() })) };
});
vi.mock("echarts/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("echarts/core")>();
  return { ...actual, init, use: vi.fn() };
});
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({ GridComponent: {}, MarkAreaComponent: {}, TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

import { SOURCE_ID, point, profile } from "@/components/identity/fixtures";
import { ApiError, api, identityApi, type CallerInfo, type IdentityProfile, type IdentitySeriesPoint } from "@/lib/api";
import * as pivot from "@/lib/pivot";

import IdentitySourceError from "./error";
import Loading from "./loading";
import IdentitySourcePage from "./page";

const caller = (perms: string[]): CallerInfo =>
  ({ subject: "analyst", email: null, permissions: perms, effective_permissions: perms }) as CallerInfo;

// The fixtures use fixed bucket times, and the page derives its window from `Date.now()`; pinning
// the clock keeps the points inside the window on any day the suite runs.
const NOW = new Date("2026-11-13T10:00:00Z");

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  vi.restoreAllMocks();
  setOption.mockClear();
  init.mockClear();
});
afterEach(() => vi.useRealTimers());

function names(): IdentitySeriesPoint[] {
  return [
    point({
      bucket_start: "2026-11-13T09:50:00Z",
      event_count: 30,
      usernames: [
        { rank: 1, username: "svc-backup", count: 20, truncated: false },
        { rank: 2, username: "CORP\\alice", count: 5, truncated: true },
      ],
    }),
    point({
      bucket_start: "2026-11-13T09:55:00Z",
      event_count: 9,
      usernames: [{ rank: 1, username: "svc-backup", count: 6, truncated: false }],
    }),
  ];
}

async function renderPage(
  series: IdentitySeriesPoint[] = names(),
  over: { profile?: IdentityProfile; perms?: string[]; params?: Record<string, string> } = {},
) {
  const read = vi
    .spyOn(identityApi, "source")
    .mockResolvedValue({ profile: over.profile ?? profile(), series });
  vi.spyOn(api, "caller").mockResolvedValue(caller(over.perms ?? ["read:identity"]));
  vi.spyOn(pivot.pivotReads, "logSource").mockResolvedValue(null);
  const view = render(
    await IdentitySourcePage({
      params: Promise.resolve({ id: SOURCE_ID }),
      searchParams: Promise.resolve(over.params ?? {}),
    }),
  );
  return { ...view, read };
}

describe("Identity source", () => {
  it("has one h1 named for the source and reads a bounded window", async () => {
    const { read, container } = await renderPage();
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("dc-01.example.test");
    const [, w] = read.mock.calls[0];
    expect(Date.parse(w!.end!) - Date.parse(w!.start!)).toBe(24 * 3_600_000);
  });

  it("lists the stored usernames as the rows hold them, with a timeline link", async () => {
    await renderPage();
    const table = screen.getByRole("table", { name: /Usernames stored for event 4625/ });
    const link = within(table).getByRole("link", { name: "svc-backup" });
    expect(link).toHaveAttribute("href", "/entities?kind=user&value=svc-backup");
    expect(within(table).getByRole("link", { name: "CORP\\alice" })).toHaveAttribute(
      "href",
      "/entities?kind=user&value=CORP%5Calice",
    );
    const row = link.closest("tr")!;
    expect(within(row).getAllByRole("cell")[1]).toHaveTextContent("26");
    expect(within(row).getAllByRole("cell")[1]).toHaveClass("num");
    expect(within(table).getByText("Stored text cut at 255 characters")).toBeInTheDocument();
  });

  it("says names below 3 events are not stored for 4625 and only the top 20 are kept", async () => {
    await renderPage();
    expect(screen.getByText(/Names below 3 events in a bucket are not stored/)).toBeInTheDocument();
    expect(screen.getByText(/Only the top 20 per bucket are kept/)).toBeInTheDocument();
  });

  it("says every name is stored for a rare event, with no floor sentence", async () => {
    await renderPage(
      [point({ event_id: 1102, usernames: [{ rank: 1, username: "admin1", count: 1, truncated: false }] })],
      { params: { event: "1102" } },
    );
    expect(screen.getByText(/Every name is stored from one event/)).toBeInTheDocument();
    expect(screen.queryByText(/not stored, so/)).toBeNull();
  });

  it("does not invent a name: an event with no stored name says so", async () => {
    await renderPage([point({ usernames: [] })]);
    expect(screen.getByText("No username is stored for this event in the range.")).toBeInTheDocument();
  });

  it("warns that a cut name search leaves the list incomplete", async () => {
    await renderPage([point({ names_measurement: "TRUNCATED", admission_truncated: true, usernames: [] })]);
    expect(screen.getByText(/name search in this range was cut/)).toBeInTheDocument();
  });

  it("draws the chart in events per 5 minutes with no baseline", async () => {
    await renderPage();
    expect(init).toHaveBeenCalledTimes(1);
    const option = setOption.mock.calls[0][0] as { yAxis: { name: string } };
    expect(option.yAxis.name).toBe("events per 5 min");
    expect(screen.getByRole("img", { name: /Counts of event 4625 Failed logon in events per 5 min/ })).toBeInTheDocument();
    expect(screen.getByText(/No expected range is drawn/)).toBeInTheDocument();
  });

  it("draws no chart and no zero when nothing was measured", async () => {
    await renderPage([point({ measurement: "NOT_COLLECTED", event_count: null })]);
    expect(init).not.toHaveBeenCalled();
    expect(screen.getByText("No count was measured in this range.")).toBeInTheDocument();
    const events = screen.getByRole("table", { name: /Catalogue events of this source/ });
    expect(within(events).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("says a truncated bucket is a floor", async () => {
    await renderPage([point({ measurement: "TRUNCATED", event_count: 500 })]);
    expect(screen.getByText(/at their floor|at its floor|at its floor \(shaded\)/)).toBeInTheDocument();
    expect(screen.getAllByText(/≥ 500/).length).toBeGreaterThan(0);
  });

  it("picks the event from the URL and ignores one outside the profile", async () => {
    await renderPage(names(), { profile: profile({ enabled_event_ids: [4625, 4740] }), params: { event: "1102" } });
    expect(screen.getByRole("heading", { name: "Counts of 4625 Failed logon" })).toBeInTheDocument();
  });

  it("says a profile that is not collected, and still shows what was stored", async () => {
    await renderPage(names(), { profile: profile({ enabled: false }) });
    expect(screen.getByText(/profile is not enabled, so the source is not collected/)).toBeInTheDocument();
  });
});

describe("Identity source: profile controls", () => {
  it("offers no write control without admin:identity and names the permission", async () => {
    await renderPage(names(), { perms: ["read:identity"] });
    expect(screen.queryByRole("button", { name: "Validate" })).toBeNull();
    expect(screen.getByText(/Changing the profile needs the admin:identity permission/)).toBeInTheDocument();
  });

  it("offers Set, Validate and Disable to admin:identity", async () => {
    await renderPage(names(), { perms: ["read:identity", "admin:identity"] });
    expect(screen.getByRole("button", { name: "Edit mapping" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Validate" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disable" })).toBeInTheDocument();
  });

  it("withholds the controls when the grants could not be read", async () => {
    vi.spyOn(identityApi, "source").mockResolvedValue({ profile: profile(), series: names() });
    vi.spyOn(api, "caller").mockRejectedValue(new ApiError(500, "x"));
    vi.spyOn(pivot.pivotReads, "logSource").mockResolvedValue(null);
    render(await IdentitySourcePage({ params: Promise.resolve({ id: SOURCE_ID }), searchParams: Promise.resolve({}) }));
    expect(screen.queryByRole("button", { name: "Validate" })).toBeNull();
  });
});

describe("Identity source: states", () => {
  const params = { params: Promise.resolve({ id: SOURCE_ID }), searchParams: Promise.resolve({}) };

  it("opens the profile form for a log source that has none, for an administrator", async () => {
    vi.spyOn(identityApi, "source").mockRejectedValue(
      Object.assign(new ApiError(404, "profile", "No identity profile", { detail_code: "identity.profile.not_found" })),
    );
    vi.spyOn(api, "logSource").mockResolvedValue({ name: "dc-09" } as never);
    vi.spyOn(api, "caller").mockResolvedValue(caller(["admin:identity"]));
    render(await IdentitySourcePage(params));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("dc-09");
    expect(screen.getByText("This log source has no identity profile.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set mapping" })).toBeInTheDocument();
  });

  it("tells a reader without admin:identity what is missing", async () => {
    vi.spyOn(identityApi, "source").mockRejectedValue(
      new ApiError(404, "profile", "x", { detail_code: "identity.profile.not_found" }),
    );
    vi.spyOn(api, "logSource").mockResolvedValue({ name: "dc-09" } as never);
    vi.spyOn(api, "caller").mockResolvedValue(caller(["read:identity"]));
    render(await IdentitySourcePage(params));
    expect(screen.getByText(/needs the admin:identity permission/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set mapping" })).toBeNull();
  });

  it("says a missing log source was not found", async () => {
    vi.spyOn(identityApi, "source").mockRejectedValue(
      new ApiError(404, "no", "x", { detail_code: "identity.source.not_found" }),
    );
    render(await IdentitySourcePage(params));
    expect(screen.getByRole("alert")).toHaveTextContent("This log source was not found");
  });

  it("names read:identity when forbidden", async () => {
    vi.spyOn(identityApi, "source").mockRejectedValue(
      new ApiError(403, "forbidden", "missing required permission: read:identity"),
    );
    render(await IdentitySourcePage(params));
    expect(screen.getByRole("alert")).toHaveTextContent("read:identity");
  });

  it("says what failed on a server error without the exception", async () => {
    vi.spyOn(identityApi, "source").mockRejectedValue(new ApiError(500, "ECONNREFUSED 10.0.0.9"));
    render(await IdentitySourcePage(params));
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
    expect(screen.getByRole("alert")).not.toHaveTextContent("ECONNREFUSED");
  });

  it("has a loading boundary and a retrying error boundary", () => {
    const { unmount } = render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the identity source");
    unmount();
    const reset = vi.fn();
    render(<IdentitySourceError error={Object.assign(new Error("secret"), { digest: "r" })} reset={reset} />);
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret");
    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalled();
  });
});
