import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { wafBucket, wafDetail, wafDetection } from "./fixtures";
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

function view(over = {}, buckets: ReturnType<typeof wafBucket>[] | null = [wafBucket()]) {
  return render(<WafInvestigation anomaly={wafDetail(over)} buckets={buckets} />);
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
        percentage_point_change: 0.297,
        ratio: 15.1,
      }),
    });
    const m = within(screen.getByLabelText("Incident measurements"));
    expect(m.getByText("31.8%")).toBeInTheDocument();
    expect(m.getByText("2.1%")).toBeInTheDocument();
    expect(m.getByText("+29.7 pp")).toBeInTheDocument();
    expect(m.getByText(/318 of 1,000/)).toBeInTheDocument();
  });

  it("renders missing detector figures as em dashes, not zeros", () => {
    view({ detection: null, observed_value: null, expected_value: null, deviation_ratio: null, absolute_delta: null, confidence: null });
    const m = within(screen.getByLabelText("Incident measurements"));
    expect(m.getAllByText("—").length).toBeGreaterThanOrEqual(4);
    expect(m.queryByText("0")).not.toBeInTheDocument();
  });

  it("states the endpoint coverage the detector saw", () => {
    view();
    expect(screen.getByText("97.2%")).toBeInTheDocument();
  });
});

describe("WAF investigation: timeline", () => {
  it("hands the chart the detector's expected range for a volume detector", () => {
    view();
    expect(chartProps.last).toMatchObject({ expected: 41.5, expectedLow: 22, expectedHigh: 68 });
  });

  it("does not put a proportion on the request-count axis", () => {
    view({
      anomaly_type: "WAF_BLOCK_RATIO_SPIKE",
      expected_value: 0.021,
      detection: wafDetection({ expected_requests: null, expected_proportion: 0.021 }),
    });
    expect(chartProps.last).toMatchObject({ expected: null, expectedLow: null, expectedHigh: null });
    expect(screen.getByText(/judges a proportion of requests, not their count/)).toBeInTheDocument();
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

  it("states every evidence status in words, as Phase A does", () => {
    for (const status of ["NOT_REQUESTED", "PENDING", "PARTIAL", "UNAVAILABLE", "FAILED"] as const) {
      const { unmount } = view({ evidence_status: status });
      expect(screen.getByText(`${status}.`)).toBeInTheDocument();
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

  it("uses no causal language in its summary", () => {
    view();
    const text = document.body.textContent!.toLowerCase();
    for (const word of ["caused by", "root cause", "attacker", "compromise", "malicious", "because of"]) {
      expect(text).not.toContain(word);
    }
  });
});
