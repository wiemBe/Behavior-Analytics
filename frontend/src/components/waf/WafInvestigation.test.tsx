import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  wafBaselineCell,
  wafBucket,
  wafDetail,
  wafDetection,
  wafNewEndpointDetection,
  wafRatioDetection,
  wafSuppressedDetail,
} from "./fixtures";
import { setTestLocale } from "../../../vitest.setup";
import { buildBaselineLookup, type BaselineSource } from "./wafBaseline";
import { NO_VIRTUAL_HOST_SENTENCE } from "./WafIncidentHeader";
import { WafInvestigation } from "./WafInvestigation";
import { endpointRef } from "./fixtures";

// The chart's own behaviour is covered in WafRequestChart.test.tsx. Here it is
// stubbed down to what this view hands it.
const chartProps = vi.hoisted(() => ({ last: null as null | Record<string, unknown> }));
vi.mock("./WafRequestChart", () => ({
  WafRequestChart: (props: Record<string, unknown>) => {
    chartProps.last = props;
    return (
      <>
        <div role="img" aria-label="chart" />
        <p>{String(props.textSummary ?? "")}</p>
      </>
    );
  },
}));

function view(
  over = {},
  buckets: ReturnType<typeof wafBucket>[] | null = [wafBucket()],
  baselines: ReturnType<typeof wafBaselineCell>[] | null = null,
) {
  return render(<WafInvestigation anomaly={wafDetail(over)} buckets={buckets} baselines={baselines} />);
}

describe("WAF investigation: identity", () => {
  it("has one h1, naming method and endpoint, and names the source and virtual host", () => {
    const { container } = view();
    const h1s = container.querySelectorAll("h1");
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("GET /portal/login");
    expect(screen.getByRole("link", { name: "lab-waf-behavior-01" })).toBeInTheDocument();
    expect(screen.getByText("portal.lab.example")).toBeInTheDocument();
    expect(screen.queryByText(NO_VIRTUAL_HOST_SENTENCE)).not.toBeInTheDocument();
  });

  it("names the detector as an analyst would, never as the raw enum", () => {
    view();
    expect(screen.getByText("Request-rate spike")).toBeInTheDocument();
    expect(screen.queryByText("WAF_REQUEST_RATE_SPIKE")).not.toBeInTheDocument();
  });

  it("says, in the design's own sentence, that a source with no virtual host may combine applications", () => {
    view({ waf_endpoint: endpointRef({ virtual_host: null, virtual_host_available: false }) });
    expect(screen.getByText(NO_VIRTUAL_HOST_SENTENCE)).toBeInTheDocument();
    expect(screen.queryByText(/Virtual host/)).not.toBeInTheDocument();
  });

  it("does not claim the source lacks a virtual host when only this endpoint has none", () => {
    view({ waf_endpoint: endpointRef({ virtual_host: null, virtual_host_available: true }) });
    expect(screen.queryByText(NO_VIRTUAL_HOST_SENTENCE)).not.toBeInTheDocument();
    expect(screen.getByText(/No virtual host on this endpoint/)).toBeInTheDocument();
  });

  it("gives the lifecycle state the strong badge and severity a quiet one", () => {
    const { container } = view({ state: "OPEN" });
    const header = within(container.querySelector("header") as HTMLElement);
    expect(header.getByText("OPEN").className).toContain("pill-strong");
    expect(header.getByText("HIGH").className).toContain("pill-quiet");
  });

  it("says a still-running incident has not ended", () => {
    view({ anomaly_end: null, duration_seconds: null });
    expect(screen.getByText(/still running/)).toBeInTheDocument();
    expect(within(screen.getByLabelText("Incident measurements")).getByText("—")).toBeInTheDocument();
  });
});

describe("WAF investigation: the suppression reason (POL-2)", () => {
  it("shows the stored reason as sent, labelled, under the Suppressed pill", () => {
    const { container } = view(wafSuppressedDetail());
    expect(screen.getByText("Suppressed")).toBeInTheDocument();
    const line = container.querySelector("p.incident-suppression");
    expect(line).toHaveTextContent("Suppression reason: maintenance window");
  });

  it("renders a coded reason in Turkish", () => {
    setTestLocale("tr");
    const { container } = view(wafSuppressedDetail());
    expect(screen.getByText("Bastırıldı")).toBeInTheDocument();
    const line = container.querySelector("p.incident-suppression");
    expect(line).toHaveTextContent("Bastırma nedeni: Bakım penceresi");
    expect(line).not.toHaveTextContent("maintenance window");
  });

  it("keeps the stored text when the row has no code (an old row)", () => {
    setTestLocale("tr");
    const { container } = view(
      wafSuppressedDetail({ suppression_reason_code: null, suppression_reason_params: null }),
    );
    expect(container.querySelector("p.incident-suppression")).toHaveTextContent(
      "Bastırma nedeni: maintenance window",
    );
  });

  it("draws nothing when the reason is empty: the pill alone", () => {
    const { container } = view(
      wafSuppressedDetail({
        suppression_reason: null,
        suppression_reason_code: null,
        suppression_reason_params: null,
      }),
    );
    expect(screen.getByText("Suppressed")).toBeInTheDocument();
    expect(container.querySelector("p.incident-suppression")).toBeNull();
  });
});

describe("WAF investigation: measurements", () => {
  it("shows an exact count as a number, with its unit", () => {
    view();
    const m = within(screen.getByLabelText("Incident measurements"));
    expect(m.getByText("412")).toBeInTheDocument();
    expect(m.getAllByText("requests / 5 min").length).toBeGreaterThan(0);
  });

  it("shows a LOWER_BOUND observed value as '≥ n', never as n", () => {
    view({ detection: wafDetection({ request_count_measurement: "LOWER_BOUND" }), observed_measurement: "LOWER_BOUND" });
    const m = within(screen.getByLabelText("Incident measurements"));
    expect(m.getByText("≥ 412")).toBeInTheDocument();
    expect(m.queryByText("412")).not.toBeInTheDocument();
    expect(screen.getByText(/lower bound/i)).toBeInTheDocument();
  });

  it("shows a proportion detector in percent, with the counts it came from", () => {
    view({
      anomaly_type: "WAF_BLOCK_RATIO_SPIKE",
      observed_value: 0.318,
      expected_value: 0.021,
      detection: wafDetection({
        observed_proportion: 0.318,
        expected_proportion: 0.021,
        observed_numerator: 318,
        observed_denominator: 1000,
        percentage_point_change: 29.7,
        ratio: 15.1,
      }),
    });
    const m = within(screen.getByLabelText("Incident measurements"));
    expect(m.getByText("31.8 %")).toBeInTheDocument();
    expect(m.getByText("2.1 %")).toBeInTheDocument();
    expect(m.getByText("+29.7 pp")).toBeInTheDocument();
    expect(m.getByText(/318 of 1,000/)).toBeInTheDocument();
  });

  it("renders the backend's percentage points as sent, never 100 times larger", () => {
    // Shape from `app/waf/behavior.py`: observed 0.397 against 0.10 is `rise` 0.297, sent as
    // `absolute_deviation: 0.297` (a fraction) and `percentage_point_change: 29.7` (points).
    view({
      anomaly_type: "WAF_BLOCK_RATIO_SPIKE",
      observed_value: 0.397,
      expected_value: 0.1,
      detection: wafDetection({
        observed_proportion: 0.397,
        expected_proportion: 0.1,
        observed_numerator: 397,
        observed_denominator: 1000,
        percentage_point_change: 29.7,
        ratio: 3.97,
      }),
    });
    const m = within(screen.getByLabelText("Incident measurements"));
    expect(m.getByText("+29.7 pp")).toBeInTheDocument();
    expect(m.queryByText(/2970/)).not.toBeInTheDocument();
  });

  it("renders missing detector figures as em dashes, not zeros", () => {
    view({ detection: null, observed_value: null, expected_value: null, deviation_ratio: null, absolute_delta: null, confidence: null });
    const m = within(screen.getByLabelText("Incident measurements"));
    expect(m.getAllByText("—").length).toBeGreaterThanOrEqual(4);
    expect(m.queryByText("0")).not.toBeInTheDocument();
  });

  it("states the endpoint coverage the detector saw", () => {
    view();
    expect(screen.getByText("97.2 %")).toBeInTheDocument();
  });
});

describe("WAF investigation: timeline", () => {
  it("hands the chart the detector's expected range for a volume detector", () => {
    view();
    expect(chartProps.last).toMatchObject({ expected: 41.5, expectedLow: 22, expectedHigh: 68 });
  });

  it("charts a ratio detector's own proportion, with its expected value as a fraction", () => {
    view({
      anomaly_type: "WAF_BLOCK_RATIO_SPIKE",
      expected_value: 0.021,
      detection: wafDetection({ expected_requests: null, expected_proportion: 0.021 }),
    });
    expect(chartProps.last!.ratio).toMatchObject({
      baselineMetric: "block_ratio",
      numerator: "blocked_count",
      denominator: "action_events",
    });
    expect(chartProps.last).toMatchObject({ expected: 0.021 });
    // The note that sent the analyst elsewhere for the proportion is gone.
    expect(screen.queryByText(/judges a proportion of requests, not their count/)).not.toBeInTheDocument();
  });

  it("charts requests, with no ratio, for a volume detector", () => {
    view();
    expect(chartProps.last!.ratio).toBeNull();
  });

  it("describes partial and uncollected intervals as gaps in the text summary", () => {
    view({}, [
      wafBucket({ bucket_start: "2026-10-12T09:00:00Z" }),
      wafBucket({ bucket_start: "2026-10-12T09:05:00Z", completeness: "PARTIAL", partial: true }),
      wafBucket({ bucket_start: "2026-10-12T09:30:00Z", request_count_measurement: "LOWER_BOUND" }),
    ]);
    const text = String(chartProps.last!.textSummary);
    expect(text).toMatch(/drawn as gaps, not as zero requests/);
    expect(text).toMatch(/1 buckets are lower bounds/);
  });

  it("says the history could not be loaded, rather than drawing an empty chart", () => {
    view({}, null);
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be loaded/);
    expect(screen.queryByRole("img", { name: "chart" })).not.toBeInTheDocument();
  });
});

describe("WAF investigation: working, evidence and lifecycle", () => {
  it("shows the detector's recorded working, with missing fields as em dashes", () => {
    view({ detection: wafDetection({ baseline_completeness: null, normalizer_version: null }) });
    expect(screen.getByText(/412 requests in the 09:00 bucket/)).toBeInTheDocument();
    const working = within(screen.getByRole("heading", { name: "Against what normal" }).closest("section")!);
    expect(working.getByText("22 – 68")).toBeInTheDocument();
    expect(working.getByText("31")).toBeInTheDocument();
    // Fields the detector did not record are unmeasured, not zero.
    const completeness = working.getByText("Baseline completeness").closest("div")!;
    expect(completeness).toHaveTextContent("—");
    const normalizer = working.getByText("Normalizer version").closest("div")!;
    expect(normalizer).toHaveTextContent("—");
  });

  it("says no working was recorded rather than showing an empty baseline", () => {
    view({ detection: null });
    expect(screen.getByText(/No detector working was recorded/)).toBeInTheDocument();
  });

  it("states INSUFFICIENT_DATA as not judged, never as healthy", () => {
    view({ state: "INSUFFICIENT_DATA", detection: null });
    expect(screen.getByText(/not being judged, which is not the same as being healthy/)).toBeInTheDocument();
    expect(screen.getByText("INSUFFICIENT_DATA").className).not.toMatch(/\bok\b/);
  });

  it("has no Evidence section: WAF anomalies are never given an evidence request", () => {
    for (const status of ["NOT_REQUESTED", "PENDING", "PARTIAL", "UNAVAILABLE", "FAILED"] as const) {
      const { unmount } = view({ evidence_status: status });
      expect(screen.queryByRole("heading", { name: "Evidence" })).not.toBeInTheDocument();
      expect(screen.queryByText(/evidence collection/i)).not.toBeInTheDocument();
      unmount();
    }
  });

  it("omits the Phase A-only sections rather than rendering them empty", () => {
    view();
    expect(screen.queryByText("What changed")).not.toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByText(/AQL/)).not.toBeInTheDocument();
  });

  it("lists every transition in order", () => {
    view();
    const list = screen.getByRole("list");
    expect(within(list).getAllByText("CANDIDATE").length).toBeGreaterThan(0);
    expect(screen.getByText("first abnormal bucket")).toBeInTheDocument();
    expect(screen.getByText("3 consecutive abnormal buckets")).toBeInTheDocument();
  });

  it("shows both ends of a lifecycle transition as the backend names them", () => {
    const base = wafDetail().transitions[0];
    view({
      transitions: [{ ...base, from_state: "INSUFFICIENT_DATA", to_state: "NORMAL" }],
    });
    const lifecycle = within(screen.getByRole("heading", { name: "Lifecycle" }).closest("section")!);
    expect(lifecycle.getByText("INSUFFICIENT_DATA")).toBeInTheDocument();
    expect(lifecycle.getByText("NORMAL")).toBeInTheDocument();
    expect(lifecycle.queryByText("Insufficient data")).not.toBeInTheDocument();
    expect(lifecycle.queryByText("Normal")).not.toBeInTheDocument();
  });

  it("uses no causal language in its summary", () => {
    view();
    const text = document.body.textContent!.toLowerCase();
    for (const word of ["caused by", "root cause", "attacker", "compromise", "malicious", "because of"]) {
      expect(text).not.toContain(word);
    }
  });
});

// The chart gets plain data (a function cannot cross from a server component).
function lookupOf(baseline: unknown) {
  const b = baseline as BaselineSource;
  return buildBaselineLookup(b.cells, b.metric, b.normalizerVersion);
}

describe("WAF investigation: seasonal band and ratio", () => {
  it("hands the chart a per-bucket lookup built from the cells, for a volume detector", () => {
    view({}, [wafBucket()], [wafBaselineCell({ weekday: 1, hour: 9, median: 50, p05: 30, p95: 80 })]);
    const lookup = lookupOf(chartProps.last!.baseline);
    expect(lookup(Date.parse("2026-10-12T09:05:00Z"))).toEqual({ median: 50, low: 30, high: 80 });
    expect(lookup(Date.parse("2026-10-12T10:05:00Z"))).toBeNull();
  });

  it("hands the chart no lookup when the cells could not be loaded", () => {
    view({}, [wafBucket()], null);
    expect(chartProps.last!.baseline).toBeNull();
  });

  it("builds a ratio detector's lookup from the ratio cells, not request_count", () => {
    view(
      { anomaly_type: "WAF_BLOCK_RATIO_SPIKE", detection: wafDetection({ expected_proportion: 0.02 }) },
      [wafBucket()],
      [
        wafBaselineCell({ metric_name: "request_count", median: 50 }),
        wafBaselineCell({ metric_name: "block_ratio", median: 0.02, p05: 0.01, p95: 0.05 }),
      ],
    );
    const lookup = lookupOf(chartProps.last!.baseline);
    expect(lookup(Date.parse("2026-10-12T09:05:00Z"))).toEqual({ median: 0.02, low: 0.01, high: 0.05 });
  });

  it("gives a novelty detector no seasonal band: its premise is no history", () => {
    view({ anomaly_type: "WAF_NEW_ENDPOINT_ACCESS" }, [wafBucket()], [wafBaselineCell()]);
    expect(chartProps.last!.baseline).toBeNull();
  });

  it("describes a ratio chart in its own quantity, and its gaps as not 0 %", () => {
    view(
      { anomaly_type: "WAF_BLOCK_RATIO_SPIKE", detection: wafDetection({ expected_proportion: 0.021 }) },
      [wafBucket({ action_measurement: "FAILED", blocked_count: null, action_events: null })],
    );
    const text = String(chartProps.last!.textSummary);
    expect(text).toMatch(/Blocked share of requests/);
    expect(text).toMatch(/expected proportion at detection was 2\.1\s%\./);
    expect(text).toMatch(/not as 0 %/);
  });
});

describe("WAF investigation: against what normal", () => {
  const working = () =>
    within(screen.getByRole("heading", { name: "Against what normal" }).closest("section")!);

  it.each([
    ["OK", "21.40"],
    [null, "21.40"],
  ] as const)("shows the robust z figure when status is %s", (status, figure) => {
    view({ detection: wafDetection({ robust_score_status: status, robust_z: 21.4 }) });
    expect(working().getByText("Robust z").closest("div")).toHaveTextContent(figure);
  });

  it.each([
    ["DEGENERATE", "not used: MAD was zero"],
    ["UNAVAILABLE", "not computed"],
  ] as const)("withholds the stored robust z when status is %s", (status, reason) => {
    view({ detection: wafDetection({ robust_score_status: status, robust_z: 21.4 }) });
    const row = working().getByText("Robust z").closest("div")!;
    expect(row).toHaveTextContent(reason);
    expect(row).not.toHaveTextContent("21.40");
  });

  it.each([
    ["seasonal", "WAF_REQUEST_RATE_SPIKE", wafDetection],
    ["new endpoint", "WAF_NEW_ENDPOINT_ACCESS", wafNewEndpointDetection],
  ] as const)("renders and omits the detector caveat in the %s branch", (_name, type, detection) => {
    const caveat = "This detector records a change in traffic, not its cause.";
    const first = view({ anomaly_type: type, detection: detection({ caveat }) });
    expect(working().getByText(caveat)).toBeInTheDocument();
    first.unmount();

    view({ anomaly_type: type, detection: detection({ caveat: null }) });
    expect(working().queryByText(caveat)).not.toBeInTheDocument();
  });

  it("omits a caveat that is absent from an older WAF payload", () => {
    view({ detection: wafDetection() });
    expect(
      working().queryByText("This detector records a change in traffic, not its cause."),
    ).not.toBeInTheDocument();
  });

  it("gives the expected range its unit", () => {
    view();
    expect(working().getByText("Expected range at detection").closest("div")).toHaveTextContent(
      "22 – 68 requests / 5 min",
    );
  });

  it("gives a missing range a meaning rather than a bare dash", () => {
    view({ detection: wafDetection({ expected_low: null, expected_high: null }) });
    const row = working().getByText("Expected range at detection").closest("div")!;
    expect(row).toHaveTextContent("not recorded for this incident");
  });

  it("shows a ratio detector's seasonal p05-p95 as percentages, not requests", () => {
    view({
      anomaly_type: "WAF_BLOCK_RATIO_SPIKE",
      detection: wafDetection({ expected_low: 0.01, expected_high: 0.05, expected_proportion: 0.02 }),
    });
    const row = working().getByText("Seasonal p05–p95 at detection").closest("div")!;
    expect(row).toHaveTextContent("1.0 % – 5.0 %");
    expect(row).not.toHaveTextContent("requests / 5 min");
  });
});

const NEW_ENDPOINT = {
  anomaly_type: "WAF_NEW_ENDPOINT_ACCESS",
  observed_value: 320,
  expected_value: 0,
  deviation_ratio: null,
  robust_z: null,
  absolute_delta: 320,
  consecutive_buckets: 4,
  confidence: 0.8,
  detection: wafNewEndpointDetection(),
};

describe("WAF investigation: new endpoint access", () => {
  const measures = () => within(screen.getByRole("region", { name: "Incident measurements" }));
  const working = () =>
    within(screen.getByRole("heading", { name: "Against what normal" }).closest("section")!);

  it("reads the observed count as requests since the endpoint first appeared, not per bucket", () => {
    view(NEW_ENDPOINT);
    const observed = measures().getByText("Observed").closest("div")!;
    expect(observed).toHaveTextContent("320requests since it first appeared");
    expect(observed).toHaveTextContent("not a per-bucket rate");
    expect(observed).not.toHaveTextContent("/ 5 min");
    expect(screen.getByText(/received 320 requests since it first appeared/)).toBeInTheDocument();
  });

  it("reads the expected value as none, never 0", () => {
    view(NEW_ENDPOINT);
    const expected = measures().getByText("Expected").closest("div")!;
    expect(expected).toHaveTextContent("none");
    expect(expected).not.toHaveTextContent(/\b0\b/);
    expect(expected).not.toHaveTextContent("requests");
    expect(expected).toHaveTextContent("no seasonal baseline applies");
    expect(expected).not.toHaveTextContent("Seasonal baseline, this weekday and hour");
  });

  it("gives deviation an em dash and says nothing was expected", () => {
    view(NEW_ENDPOINT);
    const deviation = measures().getByText("Deviation").closest("div")!;
    expect(deviation).toHaveTextContent("—");
    expect(deviation).toHaveTextContent("Not defined: nothing was expected");
  });

  it("omits endpoint coverage, which this detector does not record", () => {
    view(NEW_ENDPOINT);
    expect(measures().queryByText("Endpoint coverage")).not.toBeInTheDocument();
  });

  it("does not carry the zero-variability confidence note", () => {
    view({ ...NEW_ENDPOINT, detection: wafNewEndpointDetection({ robust_score_status: "DEGENERATE" }) });
    expect(measures().queryByText(/limited because baseline variability is zero/)).not.toBeInTheDocument();
  });

  it("shows the materiality floor and the verified history, and the backend's reason", () => {
    view(NEW_ENDPOINT);
    expect(working().getByText(/received 320 requests across 4 buckets/)).toBeInTheDocument();
    expect(working().getByText("Materiality floor").closest("div")).toHaveTextContent(
      "material at ≥ 50 requests",
    );
    const history = working().getByText("Verified history").closest("div")!;
    expect(history).toHaveTextContent("288 buckets");
    expect(history).toHaveTextContent("below the admission threshold");
  });

  it("has no robust-score line, robust z, surprise threshold or seasonal-baseline items", () => {
    view({
      ...NEW_ENDPOINT,
      // Even if a payload carried them, a detector with no seasonal premise draws none.
      detection: wafNewEndpointDetection({
        robust_score_status: "OK",
        robust_z: 9.1,
        expected_low: 10,
        expected_high: 20,
        baseline_completeness: 0.9,
        baseline_version: 6,
      }),
    });
    for (const label of [
      "Robust z",
      "Robust z threshold",
      "Surprise threshold",
      "Expected range at detection",
      "Baseline samples",
      "Baseline completeness",
      "Baseline version",
    ]) {
      expect(working().queryByText(label)).not.toBeInTheDocument();
    }
    expect(working().queryByText(/Robust score/)).not.toBeInTheDocument();
    expect(working().queryByText(/±/)).not.toBeInTheDocument();
  });

  it("says when the floor or the history length was not recorded, rather than a dash alone", () => {
    view({ ...NEW_ENDPOINT, detection: wafNewEndpointDetection({ threshold: null, baseline_sample_count: null }) });
    expect(working().getByText("Materiality floor").closest("div")).toHaveTextContent(
      "no materiality floor was recorded",
    );
    expect(working().getByText("Verified history").closest("div")).toHaveTextContent(
      "no history length was recorded",
    );
  });

  it("hands the chart no expected line and no band, and keeps its axis per bucket", () => {
    view(NEW_ENDPOINT, [wafBucket()], [wafBaselineCell()]);
    expect(chartProps.last).toMatchObject({ expected: null, expectedLow: null, expectedHigh: null });
    expect(chartProps.last?.baseline).toBeNull();
  });
});

describe("WAF investigation: the surprise threshold is one-sided", () => {
  const working = () =>
    within(screen.getByRole("heading", { name: "Against what normal" }).closest("section")!);

  it.each([
    ["WAF_REQUEST_RATE_SPIKE", wafDetection()],
    ["WAF_BLOCK_RATIO_SPIKE", wafRatioDetection()],
    ["WAF_ERROR_RATIO_SPIKE", wafRatioDetection()],
    ["WAF_CLIENT_ERROR_RATIO_SPIKE", wafRatioDetection()],
    ["WAF_CLIENT_CONCENTRATION_SPIKE", wafRatioDetection()],
  ])("%s reads the threshold as +3.50 against the robust z", (type, detection) => {
    view({ anomaly_type: type, detection });
    const row = working().getByText("Robust z threshold").closest("div")!;
    expect(row).toHaveTextContent("+3.50");
    expect(row).not.toHaveTextContent("±");
    expect(working().queryByText("Surprise threshold")).not.toBeInTheDocument();
  });

  it("shows no threshold sign when none was recorded", () => {
    view({ detection: wafDetection({ threshold: null }) });
    expect(working().getByText("Robust z threshold").closest("div")).toHaveTextContent("—");
  });
});
