// SOC-1b: every family's anomaly page carries the triage block, and the page reads it, the
// caller's grants and the alert that names the anomaly without ever breaking the investigation.

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomalyDetailPage from "./page";
import { ipsDetail } from "@/components/ips/fixtures";
import { pwDetail } from "@/components/propertywatch/fixtures";
import { wafDetail } from "@/components/waf/fixtures";
import {
  ApiError,
  api,
  type Alert,
  type AnomalyDetail,
  type Triage,
  type TriageFamily,
} from "@/lib/api";

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

function triage(family: TriageFamily, id: string, over: Partial<Triage> = {}): Triage {
  return {
    family,
    anomaly_id: id,
    status: "IN_PROGRESS",
    owner: "k.osei",
    disposition: null,
    disposition_note: null,
    updated_by: "k.osei",
    updated_at: "2026-11-05T18:50:00Z",
    version: 1,
    events: {
      items: [
        {
          id: "e-1",
          kind: "COMMENT",
          actor: "k.osei",
          created_at: "2026-11-05T18:50:00Z",
          from_value: null,
          to_value: null,
          comment: "Looking at it.",
          triage_version: 1,
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    },
    ...over,
  };
}

function alertFor(anomalyId: string): Alert {
  return {
    id: "al-9",
    fingerprint: "fp",
    title: "VOLUME_SPIKE on LAB Firewall",
    title_code: "alert.anomaly.title",
    title_params: { anomaly_type: "VOLUME_SPIKE", log_source: "LAB Firewall" },
    description: null,
    severity: "HIGH",
    status: "OPEN",
    source_type: "ANOMALY",
    opened_at: "2026-11-05T18:36:00Z",
    first_seen_at: "2026-11-05T18:36:00Z",
    last_seen_at: null,
    acknowledged_by: null,
    resolved_by: null,
    resolution_reason: null,
    occurrence_count: 1,
    evidence_snapshot: {},
    source_anomaly_ids: [anomalyId],
  };
}

let triageRead: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.restoreAllMocks();
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

async function renderFor(detail: AnomalyDetail, family: TriageFamily, read?: Triage) {
  vi.spyOn(api, "anomaly").mockResolvedValue(detail);
  triageRead = vi.spyOn(api, "triage").mockResolvedValue(read ?? triage(family, detail.id));
  return render(await AnomalyDetailPage({ params: Promise.resolve({ id: detail.id }) }));
}

describe("each family's detail renders the triage block", () => {
  const cases: Array<[string, () => AnomalyDetail, TriageFamily]> = [
    ["Phase A (log source)", () => phaseA(), "log_source"],
    ["WAF endpoint", () => wafDetail() as unknown as AnomalyDetail, "waf"],
    ["IPS entity", () => ipsDetail() as unknown as AnomalyDetail, "ips"],
    ["property-watch value", () => pwDetail() as unknown as AnomalyDetail, "property_watch"],
  ];

  it.each(cases)("%s", async (_name, make, family) => {
    const detail = make();
    await renderFor(detail, family);
    expect(triageRead).toHaveBeenCalledWith(family, detail.id, { limit: 50 });
    const section = screen.getByRole("heading", { name: "Triage" }).closest("section")!;
    expect(within(section).getByText("In progress")).toBeInTheDocument();
    expect(within(section).getByText("k.osei", { selector: "dd" })).toBeInTheDocument();
    expect(within(section).getByText("Looking at it.")).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Take it" })).toBeInTheDocument();
  });

  it("keeps the detector's lifecycle state and the analyst's status apart", async () => {
    await renderFor(phaseA({ state: "OPEN" }), "log_source");
    // The page header's pill is the detector's; the panel's is labelled as the analyst's.
    expect(screen.getByText("OPEN", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByText("Triage status")).toBeInTheDocument();
  });
});

describe("the caller's access", () => {
  it("is read-only without anomaly:triage and names the permission", async () => {
    vi.spyOn(api, "caller").mockResolvedValue({
      subject: "viewer",
      permissions: ["read:*"],
      effective_permissions: ["read:anomalies"],
    } as unknown as Awaited<ReturnType<typeof api.caller>>);
    await renderFor(phaseA(), "log_source");
    expect(screen.getByText(/needs the anomaly:triage permission/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Take it" })).not.toBeInTheDocument();
  });

  it("switches changes off when the grants cannot be read", async () => {
    vi.spyOn(api, "caller").mockRejectedValue(new ApiError(500, "boom"));
    await renderFor(phaseA(), "log_source");
    expect(screen.getByText(/permissions could not be checked/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Take it" })).not.toBeInTheDocument();
  });
});

describe("failures never break the investigation", () => {
  it("says the triage could not be loaded and still shows the page", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(phaseA());
    vi.spyOn(api, "triage").mockRejectedValue(new ApiError(500, "boom"));
    const { container } = render(
      await AnomalyDetailPage({ params: Promise.resolve({ id: "a-1" }) }),
    );
    expect(container.querySelector("h1")).toHaveTextContent("LAB Firewall");
    expect(screen.getByText(/triage of this anomaly could not be loaded/)).toBeInTheDocument();
    expect(screen.queryByText("New")).not.toBeInTheDocument();
  });

  it("shows nothing broken for a kind that has no triage", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(
      phaseA({ subject_kind: "CUSTOM_RULE" as never }),
    );
    const read = vi.spyOn(api, "triage");
    render(await AnomalyDetailPage({ params: Promise.resolve({ id: "a-1" }) }));
    expect(read).not.toHaveBeenCalled();
    expect(screen.getByText(/Triage is not available for this kind of anomaly/)).toBeInTheDocument();
  });
});

describe("the alert that names the anomaly (QW4)", () => {
  it("links it with the human title", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue({
      items: [alertFor("a-1")],
      total: 1,
      limit: 500,
      offset: 0,
    });
    await renderFor(phaseA(), "log_source");
    expect(screen.getByRole("link", { name: "Volume spike on LAB Firewall" })).toHaveAttribute(
      "href",
      "/alerts/al-9",
    );
  });

  it("scopes the one bounded read to the anomaly's source and detection time", async () => {
    const alerts = vi.spyOn(api, "alerts").mockResolvedValue({ items: [], total: 0, limit: 500, offset: 0 });
    await renderFor(phaseA(), "log_source");
    expect(alerts).toHaveBeenCalledTimes(1);
    expect(alerts.mock.calls[0][0]).toMatchObject({
      limit: 500,
      log_source_id: "s-1",
      since: "2026-11-05T17:35:00.000Z",
    });
    expect(screen.getByText("No alert references this anomaly.")).toBeInTheDocument();
  });

  it("claims nothing when the read was capped, since the alert could be on another page", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue({ items: [alertFor("other")], total: 900, limit: 500, offset: 0 });
    await renderFor(phaseA(), "log_source");
    expect(screen.queryByText(/No alert references/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Volume spike on/ })).not.toBeInTheDocument();
  });

  it("claims nothing when the alert read failed", async () => {
    vi.spyOn(api, "alerts").mockRejectedValue(new ApiError(403, "no"));
    await renderFor(phaseA(), "log_source");
    expect(screen.queryByText(/No alert references/)).not.toBeInTheDocument();
  });
});
