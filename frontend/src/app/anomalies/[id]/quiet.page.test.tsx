// SOC-17d-ui: every family's anomaly page carries the quiet-period block, keyed by the family and
// the anomaly, and an anomaly whose subject kind has no family carries none.

import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomalyDetailPage from "./page";
import { forAnomaly } from "@/components/alerts/suppressionFixtures";
import { ipsDetail } from "@/components/ips/fixtures";
import { pwDetail } from "@/components/propertywatch/fixtures";
import { wafDetail } from "@/components/waf/fixtures";
import { ApiError, api, suppressionApi, type AnomalyDetail } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));
vi.mock("@/components/behavior/VolumeChart", () => ({ VolumeChart: () => <div /> }));
vi.mock("@/components/waf/WafRequestChart", () => ({ WafRequestChart: () => <div /> }));
vi.mock("@/components/ips/IpsSeriesChart", () => ({ IpsSeriesChart: () => <div /> }));
vi.mock("@/components/propertywatch/PropertyWatchSeriesChart", () => ({
  PropertyWatchSeriesChart: () => <div />,
}));

function phaseA(over: Partial<AnomalyDetail> = {}): AnomalyDetail {
  return {
    id: "a-1",
    log_source_id: "s-1",
    log_source_name: "LAB Firewall",
    anomaly_type: "VOLUME_SPIKE",
    state: "OPEN",
    severity: "HIGH",
    observed_value: 6,
    expected_value: 2,
    deviation_ratio: 3,
    robust_z: 13.5,
    absolute_delta: 1200,
    consecutive_buckets: 2,
    confidence: 0.82,
    detected_at: "2026-11-05T18:35:00Z",
    opened_at: "2026-11-05T18:37:00Z",
    anomaly_start: "2026-11-05T18:35:00Z",
    anomaly_end: null,
    resolved_at: null,
    duration_seconds: null,
    evidence_status: "NOT_REQUESTED",
    suppressed: false,
    explanation: null,
    baseline_version: 1,
    policy_version: 1,
    transitions: [],
    explanation_package: null,
    detection: null,
    ...over,
  } as AnomalyDetail;
}

let quietRead: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.restoreAllMocks();
  quietRead = vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(forAnomaly({ suppression: null }));
  vi.spyOn(api, "triage").mockRejectedValue(new ApiError(500, "x"));
  vi.spyOn(api, "anomalyPivot").mockRejectedValue(new ApiError(404, "no pivot"));
  vi.spyOn(api, "caller").mockResolvedValue({
    subject: "a.rivera",
    permissions: ["admin:*"],
    effective_permissions: ["read:anomalies", "anomaly:triage"],
  } as unknown as Awaited<ReturnType<typeof api.caller>>);
  vi.spyOn(api, "alerts").mockResolvedValue({ items: [], total: 0, limit: 500, offset: 0 });
  vi.spyOn(api, "sourceMetrics").mockResolvedValue([]);
  vi.spyOn(api, "wafEndpointMetrics").mockResolvedValue({
    items: [], total: 0, limit: 2000, offset: 0, range_start: "", range_end: "",
  } as never);
  vi.spyOn(api, "wafEndpointBaselines").mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
  vi.spyOn(api, "ipsEntityMetrics").mockResolvedValue({
    items: [], total: 0, limit: 2000, offset: 0, range_start: "", range_end: "",
  } as never);
  vi.spyOn(api, "ipsEntityBaselines").mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
  vi.spyOn(api, "propertyWatchSeries").mockResolvedValue({ hours: 24, series: [] });
  vi.spyOn(api, "propertyWatchBuckets").mockResolvedValue([]);
});


async function renderFor(detail: AnomalyDetail) {
  vi.spyOn(api, "anomaly").mockResolvedValue(detail);
  const tree = await AnomalyDetailPage({ params: Promise.resolve({ id: detail.id }) });
  await act(async () => {
    render(tree);
  });
}

describe("the quiet-period block on an anomaly page", () => {
  const cases: Array<[string, () => AnomalyDetail, string]> = [
    ["Phase A (log source)", () => phaseA(), "log_source"],
    ["WAF endpoint", () => wafDetail() as unknown as AnomalyDetail, "waf"],
    ["IPS entity", () => ipsDetail() as unknown as AnomalyDetail, "ips"],
    ["property-watch value", () => pwDetail() as unknown as AnomalyDetail, "property_watch"],
  ];

  it.each(cases)("%s reads the banner for its family and anomaly", async (_name, make, family) => {
    const detail = make();
    await renderFor(detail);
    await waitFor(() => expect(quietRead).toHaveBeenCalledWith(family, detail.id));
  });

  it("carries no block for a subject kind with no family", async () => {
    await renderFor(phaseA({ subject_kind: "NEW_KIND" } as never));
    expect(quietRead).not.toHaveBeenCalled();
  });
});
