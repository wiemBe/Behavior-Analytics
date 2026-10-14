import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ipsCell,
  ipsDetail,
  ipsDetection,
  ipsEntityBucket,
  ipsSourceBucket,
  ipsSourceDetail,
} from "./fixtures";
import { ipsChartSummary, IpsInvestigation, type IpsSeriesData } from "./IpsInvestigation";
import { buildIpsEntitySeries } from "./ipsSeries";

const chartProps = vi.hoisted(() => ({ last: null as null | Record<string, unknown> }));
vi.mock("./IpsSeriesChart", () => ({
  IpsSeriesChart: (props: Record<string, unknown>) => {
    chartProps.last = props;
    return (
      <>
        <div role="img" aria-label="chart" />
        <p>{String(props.textSummary ?? "")}</p>
      </>
    );
  },
}));

const entityData = (rows = [ipsEntityBucket()]): IpsSeriesData => ({ kind: "entity", buckets: rows });
const sourceData = (rows = [ipsSourceBucket()]): IpsSeriesData => ({ kind: "source", buckets: rows });

function view(
  anomaly = ipsDetail(),
  series: IpsSeriesData | null = entityData(),
  baselines: ReturnType<typeof ipsCell>[] | null = null,
  canOpenSource = false,
) {
  return render(
    <IpsInvestigation
      anomaly={anomaly}
      series={series}
      baselines={baselines}
      canOpenSource={canOpenSource}
    />,
  );
}
const measures = () => within(screen.getByLabelText("Incident measurements"));

describe("IPS investigation: an entity incident", () => {
  it("has one h1 naming the entity, the detector label and the IPS source", () => {
    const { container } = view();
    const h1s = container.querySelectorAll("h1");
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("Destination port 445/tcp");
    expect(screen.getByText("Destination-port rate spike")).toBeInTheDocument();
    expect(screen.queryByText("IPS_PORT_RATE_SPIKE")).toBeNull();
    expect(screen.getByText("lab-ips-01")).toBeInTheDocument();
  });

  it("links the IPS source only when the caller may open it", () => {
    view(ipsDetail(), entityData(), null, false);
    expect(screen.queryByRole("link", { name: "lab-ips-01" })).toBeNull();
  });

  it("links the IPS source to its administration page for a permitted caller", () => {
    view(ipsDetail(), entityData(), null, true);
    expect(screen.getByRole("link", { name: "lab-ips-01" })).toHaveAttribute(
      "href",
      expect.stringMatching(/^\/admin\/ips-sources\/1b5c0000-/),
    );
  });

  it("shows detected, opened and resolved times with their timezone", () => {
    view();
    const header = screen.getByText("Detected").closest("div")!;
    expect(header).toHaveTextContent("UTC");
    expect(screen.getByText("Resolved").closest("div")).toHaveTextContent("—");
  });

  it("states observed and expected in events per bucket, never EPS", () => {
    const { container } = view();
    expect(measures().getByText("940")).toBeInTheDocument();
    expect(measures().getByText("120")).toBeInTheDocument();
    expect(container).toHaveTextContent("events / 5 min");
    expect(container.textContent).not.toMatch(/EPS|events per second/i);
  });

  it("says there is no evidence package and renders no contributor list", () => {
    view();
    expect(screen.getByText(/has no evidence package/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("hands the chart the seasonal cells and the recorded range", () => {
    view(ipsDetail(), entityData(), [ipsCell()]);
    expect(chartProps.last).toMatchObject({
      unit: "events",
      expected: 120,
      expectedLow: 70,
      expectedHigh: 210,
      baseline: { metric: "entity_events", pooled: false },
    });
  });

  it("renders the lifecycle transitions", () => {
    view();
    expect(screen.getByRole("heading", { name: "Lifecycle" })).toBeInTheDocument();
    expect(screen.getByText(/first abnormal bucket/)).toBeInTheDocument();
  });
});

describe("IPS investigation: states", () => {
  it("keeps the page and says the series could not be loaded when the read failed", () => {
    const { container } = view(ipsDetail(), null);
    expect(container.querySelector("h1")).toHaveTextContent("Destination port 445/tcp");
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be loaded/);
    expect(screen.queryByRole("img", { name: "chart" })).toBeNull();
    expect(measures().getByText("940")).toBeInTheDocument();
  });

  it("says no buckets were stored, as absence and not zero, when the range is empty", () => {
    view(ipsDetail(), entityData([]));
    expect(screen.getByText(/absence of collection, not an observation of zero events/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("img", { name: "chart" })).toBeNull();
  });

  it("says INSUFFICIENT_DATA when no seasonal cell is reliable, and draws no band from cells", () => {
    view(ipsDetail(), entityData(), [ipsCell({ is_reliable: false })]);
    expect(screen.getByText(/INSUFFICIENT_DATA\./)).toBeInTheDocument();
    expect(chartProps.last?.baseline).toBeNull();
  });

  it("says PARTIAL when buckets were not measured exactly", () => {
    view(
      ipsDetail(),
      entityData([
        ipsEntityBucket({ bucket_start: "2026-10-12T09:00:00Z" }),
        ipsEntityBucket({ bucket_start: "2026-10-12T09:05:00Z", measurement: "LOWER_BOUND" }),
        ipsEntityBucket({ bucket_start: "2026-10-12T09:10:00Z", measurement: null }),
      ]),
    );
    expect(screen.getByText(/PARTIAL\./)).toBeInTheDocument();
    expect(screen.getByText(/2 of 3 stored buckets were not measured exactly/)).toBeInTheDocument();
  });

  it("says TRUNCATED when admission lost entities", () => {
    view(ipsDetail(), entityData([ipsEntityBucket({ admission_truncated: true })]));
    expect(screen.getByText(/TRUNCATED\./)).toBeInTheDocument();
  });

  it("says when the seasonal cells could not be loaded", () => {
    view();
    expect(screen.getByText(/BASELINE UNAVAILABLE\./)).toBeInTheDocument();
  });

  it("reads an observed lower bound as at least n", () => {
    view(ipsDetail({ detection: ipsDetection({ observed_measurement: "LOWER_BOUND" }) }));
    expect(measures().getByText(/≥ 940/)).toBeInTheDocument();
    expect(measures().getByText(/At least this/)).toBeInTheDocument();
  });

  it("states a missing observed value as a dash with its reason, never 0", () => {
    view(
      ipsDetail({
        observed_value: null,
        detection: ipsDetection({ observed_events: null, expected_events: null, expected_low: null, expected_high: null }),
      }),
    );
    expect(measures().getByText(/not recorded for this incident/)).toBeInTheDocument();
    expect(measures().getByText(/no expected value was recorded/)).toBeInTheDocument();
    expect(measures().queryByText(/^0$/)).toBeNull();
  });

  it("says no detector working was recorded when detection is null", () => {
    view(ipsDetail({ detection: null }));
    expect(screen.getByText(/No detector working was recorded/)).toBeInTheDocument();
  });

  it("states a limited robust score", () => {
    view(ipsDetail({ detection: ipsDetection({ robust_score_status: "DEGENERATE", robust_z: null }) }));
    expect(measures().getByText(/limited because baseline variability is zero/)).toBeInTheDocument();
  });

  it("notes maintenance buckets", () => {
    view(ipsDetail(), entityData([ipsEntityBucket({ in_maintenance: true })]));
    expect(screen.getByText(/in\s+maintenance and/)).toBeInTheDocument();
  });
});

describe("IPS investigation: new destination port", () => {
  // As the backend writes it: a cumulative count judged by an event-count floor. No robust
  // score, no seasonal cell (so no completeness or baseline version).
  const a = () =>
    ipsDetail({
      anomaly_type: "IPS_NEW_DESTINATION_PORT",
      expected_value: 0,
      deviation_ratio: null,
      robust_z: null,
      detection: ipsDetection({
        reason: "destination port 445/tcp carried 310 events across 4 buckets since it first appeared",
        observed_events: 310,
        expected_events: null,
        expected_low: null,
        expected_high: null,
        threshold: 50,
        baseline_sample_count: 288,
        baseline_completeness: null,
        baseline_version: null,
        robust_score_status: null,
        robust_z: null,
        ratio: null,
        absolute_delta: 310,
        caveat: "A newly busy destination port may be a deployment as easily as a probe.",
      }),
    });

  it("expects none, charts the entity's per-bucket events, and has no seasonal band", () => {
    view(a());
    expect(measures().getByText("none")).toBeInTheDocument();
    expect(measures().getByText("310")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "chart" })).toBeInTheDocument();
    expect(chartProps.last?.baseline).toBeNull();
    // The chart axis really is per bucket.
    expect(chartProps.last?.unit).toBe("events");
    expect(screen.queryByText(/BASELINE UNAVAILABLE/)).toBeNull();
  });

  it("reads the headline as events since first appearance, never as a per-bucket rate", () => {
    const { container } = view(a());
    expect(measures().getByText("events since it first appeared")).toBeInTheDocument();
    expect(measures().getByText(/not a per-bucket rate/)).toBeInTheDocument();
    expect(container.querySelector(".incident-summary")).toHaveTextContent(
      "carried 310 events since it first appeared; none were expected",
    );
    expect(container.querySelector(".incident-summary")).not.toHaveTextContent(/\/ 5 min|\/ bucket/);
    expect(measures().queryByText(/events \/ (5 min|bucket)/)).toBeNull();
  });

  it("does not describe the deviation in events against an expectation that does not exist", () => {
    view(a());
    expect(measures().getByText(/Not defined: nothing was expected/)).toBeInTheDocument();
    expect(measures().queryByText(/events against expected/)).toBeNull();
  });

  it("words the threshold as a materiality floor, with no robust score, surprise threshold or baseline item", () => {
    const { container } = view(a());
    expect(screen.getByText("Materiality floor")).toBeInTheDocument();
    expect(screen.getByText(/material at ≥ 50 events/)).toBeInTheDocument();
    expect(screen.getByText(/288 buckets/)).toBeInTheDocument();
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/±|Surprise threshold|Robust|robust z/i);
    expect(text).not.toMatch(/Baseline (samples|completeness|version)|Expected range/);
    expect(measures().queryByText(/limited because baseline variability/)).toBeNull();
    expect(screen.getByText(/may be a deployment as easily as a probe/)).toBeInTheDocument();
  });
});

describe("IPS investigation: what was judged, per detector", () => {
  it("a rate detector's threshold is one-sided robust z, never a plus-or-minus", () => {
    const { container } = view();
    expect(screen.getByText("Robust z threshold")).toBeInTheDocument();
    expect(screen.getByText("+3.50")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/±|Materiality floor/);
  });

  it("a share's change is shown in percentage points as the API sends it, not multiplied again", () => {
    view(ipsSourceDetail(), sourceData());
    expect(measures().getByText("+44.0 pp")).toBeInTheDocument();
    expect(measures().queryByText(/4400/)).toBeNull();
  });
});

describe("IPS investigation: source detectors", () => {
  it("concentration is a share with its numerator and denominator", () => {
    const { container } = view(ipsSourceDetail(), sourceData());
    expect(container.querySelector("h1")).toHaveTextContent("Source-level");
    expect(measures().getByText("62.0%")).toBeInTheDocument();
    expect(measures().getByText(/620 of 1,?000/)).toBeInTheDocument();
    expect(chartProps.last).toMatchObject({ unit: "share" });
    expect(container.textContent).not.toMatch(/EPS/);
  });

  it("block ratio is a share of the events carrying an action", () => {
    view(ipsSourceDetail({ anomaly_type: "IPS_BLOCK_RATIO_SHIFT" }), sourceData(), [
      ipsCell({ metric_name: "block_ratio", numerator_sum: 3, denominator_sum: 10 }),
    ]);
    expect(measures().getAllByText(/events carrying an action/).length).toBeGreaterThan(0);
    expect(chartProps.last?.baseline).toMatchObject({ metric: "block_ratio", pooled: true });
  });

  it("fan-out is distinct destination ports", () => {
    const { container } = view(
      ipsSourceDetail({
        anomaly_type: "IPS_PORT_FANOUT_SPIKE",
        detection: ipsDetection({
          observed_events: null,
          expected_events: null,
          observed_distinct_ports: 87,
          expected_distinct_ports: 9,
          expected_low: 3,
          expected_high: 15,
        }),
      }),
      sourceData(),
    );
    expect(measures().getByText("87")).toBeInTheDocument();
    expect(container).toHaveTextContent("distinct ports");
    expect(chartProps.last).toMatchObject({ unit: "ports" });
  });

  it("states a pairing the API should not produce rather than drawing the wrong series", () => {
    view(ipsSourceDetail(), entityData());
    expect(screen.getByText(/does not judge a source-level series/)).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "chart" })).toBeNull();
  });

  it("names an unknown detector as sent, claims no figures and draws no series", () => {
    const { container } = view(ipsDetail({ anomaly_type: "IPS_FUTURE", detection: ipsDetection() }));
    expect(screen.getAllByText("IPS_FUTURE").length).toBeGreaterThan(0);
    expect(screen.getByText(/has no series for the detector/)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/EPS/);
  });
});

describe("ipsChartSummary", () => {
  it("states the unit, the range at detection and what is a gap", () => {
    const points = buildIpsEntitySeries([
      ipsEntityBucket({ bucket_start: "2026-10-12T09:00:00Z" }),
      ipsEntityBucket({ bucket_start: "2026-10-12T09:20:00Z", measurement: "NOT_COLLECTED" }),
    ]);
    const text = ipsChartSummary(ipsDetail(), points, "Events");
    expect(text).toMatch(/70\.0 to 210\.0 events per bucket/);
    expect(text).toMatch(/1 intervals had no stored bucket/);
    expect(text).toMatch(/not as zero/);
  });
});
