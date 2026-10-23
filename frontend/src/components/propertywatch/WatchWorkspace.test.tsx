import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";

import { aql, bucket, catalog, incident, iso, paged, point, scope, series, value, watch, WATCH_ID } from "./fixtures";
import type { PropertyWatchSeriesChartProps } from "./PropertyWatchSeriesChart";
import { WatchWorkspace, type WatchWorkspaceProps } from "./WatchWorkspace";

// The canvas has its own test; here the chart is a slot that reports what it was given.
const lastChart = vi.hoisted(() => ({ props: null as unknown }));
vi.mock("./PropertyWatchSeriesChart", () => ({
  PropertyWatchSeriesChart: (props: PropertyWatchSeriesChartProps) => {
    lastChart.props = props;
    return (
      <div role="img" aria-label="chart">
        {props.series.map((s) => s.value_key).join("|")}
      </div>
    );
  },
}));
const chartProps = () => lastChart.props as PropertyWatchSeriesChartProps;

const items = ["a", "b", "c", "d", "e", "f"].map((k) => value(`id-${k}`, k, { events: 100 - k.charCodeAt(0) + 97 }));
const s = (k: string) => series(`id-${k}`, k, [point(0), point(1)]);

function props(over: Partial<WatchWorkspaceProps> = {}): WatchWorkspaceProps {
  return {
    watch: watch(),
    catalog: catalog(),
    hours: 24,
    rangeKey: "24h",
    sort: "events",
    offset: 0,
    buckets: [bucket(0), bucket(1)],
    values: paged(items),
    incidents: [],
    incidentsTotal: 0,
    scope: scope(),
    aql: { kind: "ok", aql: aql() },
    ruleAccess: "writer",
    initialSelected: ["id-a"],
    initialSeries: [s("a")],
    windowStart: iso(0),
    windowEnd: iso(11),
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

const plot = (k: string) => screen.getByRole("checkbox", { name: `Plot ${k}` });

describe("the selection and its series", () => {
  it("draws the initial selection without asking the API again", () => {
    const read = vi.spyOn(api, "propertyWatchSeries");
    render(<WatchWorkspace {...props()} />);
    expect(read).not.toHaveBeenCalled();
    expect(chartProps().series.map((x) => x.value_key)).toEqual(["a"]);
  });

  it("reads only the series it has not read, in one request, by id", async () => {
    const read = vi
      .spyOn(api, "propertyWatchSeries")
      .mockResolvedValue({ hours: 24, series: [s("b"), s("c")] });
    render(<WatchWorkspace {...props()} />);
    fireEvent.click(plot("b"));
    fireEvent.click(plot("c"));
    await waitFor(() => expect(chartProps().series.map((x) => x.value_key)).toEqual(["a", "b", "c"]));
    for (const [id, ids] of read.mock.calls) {
      expect(id).toBe(WATCH_ID);
      expect(ids).not.toContain("id-a");
    }
    const last = read.mock.calls.at(-1)!;
    expect(last[1]).toEqual(["id-b", "id-c"]);
    expect(last[2]).toBe(24);
  });

  it("labels the series with the value text from the table", async () => {
    vi.spyOn(api, "propertyWatchSeries").mockResolvedValue({
      hours: 24,
      series: [series("id-b", "raw-key", [point(0)])],
    });
    render(<WatchWorkspace {...props()} />);
    fireEvent.click(plot("b"));
    await waitFor(() => expect(chartProps().series).toHaveLength(2));
    expect(chartProps().series[1].value_key).toBe("b");
  });

  it("allows five values and disables the sixth", () => {
    render(
      <WatchWorkspace
        {...props({
          initialSelected: ["id-a", "id-b", "id-c", "id-d", "id-e"],
          initialSeries: ["a", "b", "c", "d", "e"].map(s),
        })}
      />,
    );
    expect(plot("f")).toBeDisabled();
    fireEvent.click(plot("a"));
    expect(plot("f")).toBeEnabled();
  });

  it("removing a value redraws without a request", () => {
    const read = vi.spyOn(api, "propertyWatchSeries");
    render(<WatchWorkspace {...props()} />);
    fireEvent.click(plot("a"));
    expect(chartProps().series).toEqual([]);
    expect(read).not.toHaveBeenCalled();
  });

  it("says the series could not be loaded, keeps what is drawn, and retries", async () => {
    const read = vi
      .spyOn(api, "propertyWatchSeries")
      .mockRejectedValueOnce(new ApiError(500, "boom"))
      .mockResolvedValueOnce({ hours: 24, series: [s("b")] });
    render(<WatchWorkspace {...props()} />);
    fireEvent.click(plot("b"));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("could not be loaded");
    expect(alert).toHaveTextContent("stay on screen");
    expect(chartProps().series.map((x) => x.value_key)).toEqual(["a"]);
    await act(async () => {
      fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    });
    await waitFor(() => expect(chartProps().series.map((x) => x.value_key)).toEqual(["a", "b"]));
    expect(read).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("passes the collected buckets and the coverage floor to the chart", () => {
    render(<WatchWorkspace {...props()} />);
    expect(chartProps().collected).toEqual([iso(0), iso(1)]);
    expect(chartProps().coverage).toMatchObject({ floor: 0.9 });
    expect(chartProps().coverage?.buckets).toHaveLength(2);
  });

  it("shades only the incidents of the plotted values", () => {
    const mine = incident({ pw_value: { ...incident().pw_value!, value_key: "a", components: ["a"] } });
    const other = incident({ id: "o", pw_value: { ...incident().pw_value!, value_key: "b", components: ["b"] } });
    render(<WatchWorkspace {...props({ incidents: [mine, other], incidentsTotal: 2 })} />);
    expect(chartProps().incidents).toHaveLength(1);
  });
});

describe("the states of the values tab", () => {
  it("says an empty window is an absence of collection", () => {
    render(<WatchWorkspace {...props({ buckets: [], values: paged([]), initialSelected: [], initialSeries: [] })} />);
    expect(chartProps().emptyText).toMatch(/absence of collection, not an observation of zero/);
    expect(screen.getByText("No value is tracked for this watch in this window.")).toBeInTheDocument();
  });

  it("asks for a selection when values exist and none is plotted", () => {
    render(<WatchWorkspace {...props({ initialSelected: [], initialSeries: [] })} />);
    expect(chartProps().emptyText).toMatch(/Select up to five values/);
  });

  it("says the values could not be loaded instead of listing none", () => {
    render(<WatchWorkspace {...props({ values: null, initialSelected: [], initialSeries: [] })} />);
    expect(screen.getByText("The tracked values could not be loaded.")).toBeInTheDocument();
    expect(screen.queryByText("No value is tracked for this watch in this window.")).toBeNull();
  });

  it("offers the four windows as links, marking the current one", () => {
    render(<WatchWorkspace {...props({ hours: 6, rangeKey: "6h" })} />);
    const nav = screen.getByRole("navigation", { name: "Window" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual([
      "1 hour", "6 hours", "24 hours", "7 days",
    ]);
    expect(within(nav).getByRole("link", { name: "6 hours" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "7 days" })).toHaveAttribute(
      "href",
      `/behavior/watches/${WATCH_ID}?range=7d`,
    );
  });

  it("states the unparsed remainder of the last bucket and never as a value", () => {
    render(<WatchWorkspace {...props({ buckets: [bucket(0), bucket(1)] })} />);
    expect(screen.getByText(/40 of 1,000 events in scope carried no/)).toBeInTheDocument();
    expect(screen.getByText(/never as a zero/)).toBeInTheDocument();
  });

  it("puts the honesty line in the values tab", () => {
    render(<WatchWorkspace {...props({ catalog: catalog({ collection_enabled: false }) })} />);
    const honesty = screen.getByRole("group", { name: /not known/ });
    expect(honesty).toHaveTextContent("PW_COLLECTION_ENABLED");
    expect(
      honesty.compareDocumentPosition(screen.getByRole("region", { name: "Series chart" })) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

describe("the tabs", () => {
  it("opens on Values and offers Incidents, Threshold rules and Definition", () => {
    render(<WatchWorkspace {...props()} />);
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["Values", "Incidents (0)", "Threshold rules (0)", "Definition"]);
    expect(screen.getByRole("tab", { name: "Values" })).toHaveAttribute("aria-selected", "true");
  });

  it("lists the rules that pin a value of this watch on the Threshold rules tab", () => {
    const rules = [
      {
        id: "r1",
        name: "Backup flood",
        pw_value_id: "id-outside-page",
        value_key: "outside-page",
        components: ["outside-page"],
        enabled: true,
      },
    ];
    render(
      <WatchWorkspace
        {...props({ watch: watch({ threshold_rules: rules }) })}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Threshold rules (1)" }));
    expect(screen.getByRole("link", { name: "Backup flood" })).toHaveAttribute(
      "href",
      "/custom-rules/r1",
    );
    expect(screen.getByText("outside-page")).toBeInTheDocument();
  });

  it("shows the generated query on the Definition tab", () => {
    render(<WatchWorkspace {...props()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Definition" }));
    expect(screen.getByText(/Generated query text\. The collector adds the time range/)).toBeInTheDocument();
  });

  it("lists this watch's incidents with their lifecycle state", () => {
    render(<WatchWorkspace {...props({ incidents: [incident()], incidentsTotal: 1 })} />);
    fireEvent.click(screen.getByRole("tab", { name: "Incidents (1)" }));
    const table = screen.getByRole("table", { name: "Incidents of this watch" });
    expect(within(table).getByText("svc-backup")).toBeInTheDocument();
    expect(within(table).getByText("Value spike")).toBeInTheDocument();
    expect(within(table).getByText("OPEN")).toBeInTheDocument();
    expect(within(table).getByText("not resolved")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/anomalies/aaaaaaaa-0000-4000-8000-000000000001");
  });

  it("says no incident is not 'all normal', and that a failed read is not 'none'", () => {
    const { unmount } = render(<WatchWorkspace {...props()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Incidents (0)" }));
    expect(screen.getByText(/No incident was detected/)).toBeInTheDocument();
    expect(screen.getByText(/not a statement that every value is normal/)).toBeInTheDocument();
    unmount();
    render(<WatchWorkspace {...props({ incidents: null, incidentsTotal: null })} />);
    fireEvent.click(screen.getByRole("tab", { name: "Incidents" }));
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
  });

  it("shows the definition read only, with the caps in force and no input", () => {
    render(<WatchWorkspace {...props()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Definition" }));
    expect(screen.getByText("Log source group: DMZ firewalls")).toBeInTheDocument();
    expect(screen.getByText("Tracked values per watch")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /save|edit/i })).toBeNull();
    expect(screen.getByRole("link", { name: "Search cadence" })).toHaveAttribute("href", "/admin/search-cadence");
  });

  it("shows the scope membership: count, inventory age, groups walked, changes and names", () => {
    render(<WatchWorkspace {...props()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Definition" }));
    expect(screen.getByText("Scope membership")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Groups walked" })).toHaveTextContent("DMZ edge");
    expect(screen.getByRole("list", { name: "Membership changes" })).toHaveTextContent("37 to 40 sources");
    expect(screen.getByRole("list", { name: "Membership changes" })).toHaveTextContent("grace window");
    expect(screen.getByRole("list", { name: "Member names" })).toHaveTextContent("fw-dmz-02");
    expect(screen.getByText(/2 h ago/)).toBeInTheDocument();
    expect(screen.getByText(/2 listed, sorted|2 of 37 are listed/)).toBeInTheDocument();
  });

  it("says a stale scope is stale and states no count it was not given", () => {
    render(
      <WatchWorkspace
        {...props({
          scope: scope({ resolved: false, state: "SCOPE_STALE", reason: "synced 9 h ago", member_count: null, members: [], members_total: null }),
        })}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Definition" }));
    expect(screen.getByText("SCOPE STALE.")).toBeInTheDocument();
    expect(screen.getByText(/synced 9 h ago/)).toBeInTheDocument();
    expect(screen.getByText("not stated")).toBeInTheDocument();
  });

  it("says the membership could not be loaded instead of drawing none", () => {
    render(<WatchWorkspace {...props({ scope: null })} />);
    fireEvent.click(screen.getByRole("tab", { name: "Definition" }));
    expect(screen.getByText(/membership could not be loaded/)).toBeInTheDocument();
  });

  it("says when the caps could not be read", () => {
    render(<WatchWorkspace {...props({ catalog: null })} />);
    fireEvent.click(screen.getByRole("tab", { name: "Definition" }));
    expect(screen.getByRole("alert")).toHaveTextContent("cardinality caps could not be read");
  });
});

describe("the window line when only history was read", () => {
  it("says the latest bucket was read from history, not collected", () => {
    render(<WatchWorkspace {...props({ buckets: [{ ...bucket(0), origin: "HISTORY" }] })} />);
    expect(screen.getByText(/Latest bucket read from QRadar history/)).toBeInTheDocument();
  });

  it("keeps the plain wording for a live bucket", () => {
    render(<WatchWorkspace {...props({ buckets: [{ ...bucket(0), origin: "LIVE" }] })} />);
    expect(screen.queryByText(/read from QRadar history/)).toBeNull();
    expect(screen.getByText(/Latest bucket/)).toBeInTheDocument();
  });
});
