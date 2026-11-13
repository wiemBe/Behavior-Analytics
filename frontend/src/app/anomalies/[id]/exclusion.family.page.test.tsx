// SOC-13f: the anomaly page of a WAF, IPS, property-watch or rule incident reads that subject's
// training exclusions, hands the triage block its control on the family's own bucket grid, shades
// the effective ones on the family's chart, and lists them where the subject has no page of its own.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomalyDetailPage from "./page";
import { StaticSession } from "@/components/auth/session";
import { CAN, exclusion } from "@/components/baseline/fixtures";
import { ipsCell, ipsDetail, ipsEntityBucket, ipsSourceBucket, ipsSourceDetail, IPS_ENTITY_ID, IPS_SOURCE_ID } from "@/components/ips/fixtures";
import { bucket, point, pwDetail, series } from "@/components/propertywatch/fixtures";
import { incident, ruleDetail, RULE_ID, section, block, span } from "@/components/rules/ruleMatchFixtures";
import { wafBucket, wafDetail } from "@/components/waf/fixtures";
import {
  ApiError,
  api,
  ruleMatchApi,
  suppressionApi,
  trainingExclusionApi,
  type AnomalyDetail,
  type Triage,
} from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));
const charts = vi.hoisted(() => ({ waf: null as unknown, ips: null as unknown, pw: null as unknown }));
vi.mock("@/components/waf/WafRequestChart", () => ({
  WafRequestChart: (p: unknown) => {
    charts.waf = p;
    return <div role="img" aria-label="waf chart" />;
  },
}));
vi.mock("@/components/ips/IpsSeriesChart", () => ({
  IpsSeriesChart: (p: unknown) => {
    charts.ips = p;
    return <div role="img" aria-label="ips chart" />;
  },
}));
vi.mock("@/components/propertywatch/PropertyWatchSeriesChart", () => ({
  PropertyWatchSeriesChart: (p: unknown) => {
    charts.pw = p;
    return <div role="img" aria-label="pw chart" />;
  },
}));
vi.mock("@/components/behavior/VolumeChart", () => ({ VolumeChart: () => <div /> }));

const triage = (family: string, id: string): Triage => ({
  family: family as Triage["family"],
  anomaly_id: id,
  status: "NEW",
  owner: null,
  disposition: null,
  disposition_note: null,
  updated_by: null,
  updated_at: null,
  version: 0,
  events: { items: [], total: 0, limit: 50, offset: 0 },
});
const paged = <T,>(items: T[]) => ({
  items,
  total: items.length,
  limit: 2000,
  offset: 0,
  range_start: "2026-10-12T03:00:00Z",
  range_end: "2026-10-12T15:00:00Z",
});
const cells = { items: [ipsCell()], total: 1, limit: 200, offset: 0 };
const noCells = { items: [], total: 0, limit: 200, offset: 0 };

let forSubject: ReturnType<typeof vi.spyOn>;
let create: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.restoreAllMocks();
  charts.waf = charts.ips = charts.pw = null;
  vi.spyOn(api, "anomalyPivot").mockRejectedValue(new ApiError(404, "no pivot"));
  vi.spyOn(suppressionApi, "forAnomaly").mockRejectedValue(new ApiError(500, "no suppression read"));
  vi.spyOn(api, "caller").mockResolvedValue({
    subject: "k.osei",
    permissions: [],
    effective_permissions: ["read:anomalies", "anomaly:triage"],
  } as unknown as Awaited<ReturnType<typeof api.caller>>);
  vi.spyOn(api, "alerts").mockResolvedValue({ items: [], total: 0, limit: 500, offset: 0 });
  vi.spyOn(api, "wafEndpointBaselines").mockResolvedValue(noCells as never);
  vi.spyOn(api, "ipsEntityBaselines").mockResolvedValue(cells);
  vi.spyOn(api, "ipsSourceBaselines").mockResolvedValue(noCells as never);
  forSubject = vi.spyOn(trainingExclusionApi, "forSubject").mockResolvedValue({ items: [], limit: 100 });
  create = vi.spyOn(trainingExclusionApi, "create");
});

async function renderFor(detail: AnomalyDetail) {
  vi.spyOn(api, "anomaly").mockResolvedValue(detail);
  vi.spyOn(api, "triage").mockResolvedValue(triage("x", detail.id));
  return render(
    <StaticSession value={CAN}>
      {await AnomalyDetailPage({ params: Promise.resolve({ id: detail.id }) })}
    </StaticSession>,
  );
}

const control = () => screen.queryByRole("button", { name: "Exclude interval from training" });
const listHeading = () => screen.queryByRole("heading", { name: "Baseline training exclusions" });
const as = (d: unknown) => d as AnomalyDetail;

const WAF_RESOLVED = {
  state: "RESOLVED" as const,
  detected_at: "2026-10-12T09:07:00Z",
  anomaly_end: "2026-10-12T09:40:00Z",
  resolved_at: "2026-10-12T09:52:00Z",
};
const EXCLUDED = (over = {}) =>
  exclusion({
    starts_at: "2026-10-12T09:00:00Z",
    ends_at: "2026-10-12T10:00:00Z",
    ...over,
  });

describe("WAF endpoint", () => {
  beforeEach(() => {
    vi.spyOn(api, "wafEndpointMetrics").mockResolvedValue(
      paged([wafBucket({ bucket_start: "2026-10-12T08:00:00Z", bucket_seconds: 600 })]) as never,
    );
  });

  it("offers the control on the endpoint's bucket grid, and reads the endpoint's exclusions", async () => {
    const d = wafDetail(WAF_RESOLVED);
    await renderFor(as(d));
    expect(forSubject).toHaveBeenCalledWith("endpoint", d.waf_endpoint.id, "waf:endpoint");
    const triageSection = screen.getByRole("heading", { name: "Triage" }).closest("section")!;
    await userEvent.click(within(triageSection).getByRole("button", { name: "Exclude interval from training" }));
    expect(await screen.findByLabelText("Start (UTC)")).toHaveValue("2026-10-12 09:00");
    expect(screen.getByLabelText("End (UTC, exclusive)")).toHaveValue("2026-10-12 10:00");
    expect(screen.getByRole("form", { name: "Exclude interval from training" })).toHaveTextContent(/from this endpoint's request baseline/);
  });

  it("posts to the WAF path", async () => {
    create.mockResolvedValue(EXCLUDED({ subject_kind: "endpoint" }));
    const d = wafDetail(WAF_RESOLVED);
    await renderFor(as(d));
    await userEvent.click(control()!);
    const end = await screen.findByLabelText("End (UTC, exclusive)");
    await userEvent.clear(end);
    await userEvent.type(end, "2026-10-12 11:00");
    await userEvent.type(screen.getByLabelText("Reason"), "scan");
    await userEvent.click(screen.getByRole("button", { name: "Exclude interval" }));
    expect(create).toHaveBeenCalledWith("w-1", expect.objectContaining({ reason: "scan" }), "waf");
  });

  it("lists the endpoint's exclusions here and shades the effective ones on the chart", async () => {
    forSubject.mockResolvedValue({
      items: [
        EXCLUDED({ subject_kind: "endpoint", scope_key: "waf:endpoint" }),
        EXCLUDED({ id: "r", state: "REVOKED", starts_at: "2026-10-10T00:00:00Z", ends_at: "2026-10-10T01:00:00Z" }),
      ],
      limit: 100,
    });
    await renderFor(as(wafDetail(WAF_RESOLVED)));
    expect(listHeading()).toBeInTheDocument();
    expect(screen.getByRole("table")).toHaveTextContent("Endpoint requests and ratios");
    expect((charts.waf as { exclusions: unknown }).exclusions).toEqual([
      { start: "2026-10-12T09:00:00Z", end: "2026-10-12T10:00:00Z" },
    ]);
    expect(screen.getByText(/Excluded from baseline training:/)).toHaveTextContent("12 Oct 09:00–10:00 UTC");
  });

  it("says the bucket width could not be read when the endpoint's series failed, and offers no button", async () => {
    vi.spyOn(api, "wafEndpointMetrics").mockRejectedValue(new ApiError(500, "down"));
    await renderFor(as(wafDetail(WAF_RESOLVED)));
    expect(control()).toBeNull();
    expect(screen.getByText(/bucket width of this endpoint's source could not be read/)).toBeInTheDocument();
  });

  it("does not take a history bucket's width for the live one", async () => {
    vi.spyOn(api, "wafEndpointMetrics").mockResolvedValue(
      paged([wafBucket({ bucket_start: "2026-10-12T08:00:00Z", bucket_seconds: 3600, origin: "HISTORY" })]) as never,
    );
    await renderFor(as(wafDetail(WAF_RESOLVED)));
    expect(control()).toBeNull();
    expect(screen.getByText(/bucket width of this endpoint's source could not be read/)).toBeInTheDocument();
  });

  it("an open anomaly says it is available once resolved", async () => {
    await renderFor(as(wafDetail()));
    expect(control()).toBeNull();
    expect(screen.getByText("Available once the anomaly is resolved.")).toBeInTheDocument();
  });

  it("a read failure is its own state: the page stays, nothing claims there are none", async () => {
    forSubject.mockRejectedValue(new ApiError(500, "boom"));
    const { container } = await renderFor(as(wafDetail(WAF_RESOLVED)));
    expect(container.querySelector("h1")).toHaveTextContent("GET /portal/login");
    expect(screen.getAllByText(/could not be loaded/).length).toBeGreaterThan(0);
    expect(screen.queryByText("No analyst training exclusions affect this baseline.")).toBeNull();
    expect((charts.waf as { exclusions: unknown[] }).exclusions).toEqual([]);
  });

  it("a novelty detector makes no read and shows nothing about training", async () => {
    await renderFor(as(wafDetail({ ...WAF_RESOLVED, anomaly_type: "WAF_NEW_ENDPOINT_ACCESS" })));
    expect(forSubject).not.toHaveBeenCalled();
    expect(control()).toBeNull();
    expect(screen.queryByText("Baseline training")).toBeNull();
  });
});

describe("IPS", () => {
  const RES = {
    state: "RESOLVED" as const,
    detected_at: "2026-10-12T09:07:00Z",
    resolved_at: "2026-10-12T09:52:00Z",
  };

  it("an entity incident: control on the entity's grid, entity list on this page, chart band", async () => {
    vi.spyOn(api, "ipsEntityMetrics").mockResolvedValue(
      paged([ipsEntityBucket({ bucket_start: "2026-10-12T08:00:00Z", bucket_seconds: 600 })]) as never,
    );
    forSubject.mockResolvedValue({
      items: [EXCLUDED({ subject_kind: "entity", scope_key: "ips:entity" })],
      limit: 100,
    });
    await renderFor(as(ipsDetail(RES)));
    expect(forSubject).toHaveBeenCalledWith("entity", IPS_ENTITY_ID, "ips:entity");
    await userEvent.click(control()!);
    expect(await screen.findByLabelText("Start (UTC)")).toHaveValue("2026-10-12 09:00");
    expect(screen.getByRole("form", { name: "Exclude interval from training" })).toHaveTextContent(/from this entity's IPS event baseline/);
    expect(listHeading()).toBeInTheDocument();
    expect((charts.ips as { exclusions: unknown[] }).exclusions).toHaveLength(1);
  });

  it("a source incident: the control here, the list on the source page", async () => {
    vi.spyOn(api, "ipsSourceMetrics").mockResolvedValue(
      paged([ipsSourceBucket({ bucket_start: "2026-10-12T08:00:00Z", bucket_seconds: 600 })]) as never,
    );
    await renderFor(as(ipsSourceDetail({ ...RES, anomaly_type: "IPS_PORT_FANOUT_SPIKE" })));
    expect(forSubject).toHaveBeenCalledWith("source", IPS_SOURCE_ID, "ips:source");
    expect(control()).toBeEnabled();
    expect(listHeading()).toBeNull();
  });

  it("a new destination port has no rate baseline: no read, no control", async () => {
    vi.spyOn(api, "ipsEntityMetrics").mockResolvedValue(paged([ipsEntityBucket()]) as never);
    await renderFor(as(ipsDetail({ ...RES, anomaly_type: "IPS_NEW_DESTINATION_PORT" })));
    expect(forSubject).not.toHaveBeenCalled();
    expect(control()).toBeNull();
    expect(screen.queryByText("Baseline training")).toBeNull();
  });
});

describe("property-watch value", () => {
  const now = Date.now();
  const RES = {
    state: "RESOLVED" as const,
    anomaly_start: new Date(now - 7200_000).toISOString(),
    detected_at: new Date(now - 7000_000).toISOString(),
    anomaly_end: new Date(now - 3700_000).toISOString(),
    resolved_at: new Date(now - 3600_000).toISOString(),
  };

  beforeEach(() => {
    vi.spyOn(api, "propertyWatchSeries").mockResolvedValue({ hours: 24, series: [series("id-sb", "svc-backup", [point(0)])] });
    vi.spyOn(api, "propertyWatchBuckets").mockResolvedValue([bucket(0)]);
  });

  it("reads the value's exclusions, lists them here, shades the chart, proposes on the 5-minute grid", async () => {
    forSubject.mockResolvedValue({
      items: [EXCLUDED({ subject_kind: "value", scope_key: "pw:value" })],
      limit: 100,
    });
    await renderFor(as(pwDetail(RES)));
    expect(forSubject).toHaveBeenCalledWith("value", "id-sb", "pw:value");
    await userEvent.click(control()!);
    const start = (await screen.findByLabelText("Start (UTC)")) as HTMLInputElement;
    expect(Number(start.value.slice(-2)) % 5).toBe(0);
    expect(screen.getByRole("form", { name: "Exclude interval from training" })).toHaveTextContent(/from this property value's baseline/);
    expect(listHeading()).toBeInTheDocument();
    expect((charts.pw as { exclusions: unknown[] }).exclusions).toHaveLength(1);
  });

  it("a new value is a novelty: no read, no control", async () => {
    await renderFor(as(pwDetail({ ...RES, anomaly_type: "PW_NEW_VALUE" })));
    expect(forSubject).not.toHaveBeenCalled();
    expect(control()).toBeNull();
  });

  it("a value that retention deleted has no subject to exclude against: no control", async () => {
    const d = pwDetail(RES);
    await renderFor(as({ ...d, pw_value: { ...d.pw_value, value_id: null } }));
    expect(forSubject).not.toHaveBeenCalled();
    expect(control()).toBeNull();
  });
});

describe("rule", () => {
  beforeEach(() => {
    vi.spyOn(ruleMatchApi, "section").mockResolvedValue(section({ incidents: [incident()] }));
  });
  const RES = { state: "RESOLVED" as const, resolved_at: "2026-11-09T10:30:00Z", anomaly_end: "2026-11-09T10:20:00Z" };

  it("offers the control on the rule's window grid, reads both scopes, lists on the rule page", async () => {
    vi.spyOn(ruleMatchApi, "anomalies").mockResolvedValue(block({ span: span({ window_seconds: 3600 }) }));
    await renderFor(as(ruleDetail(RES)));
    expect(forSubject).toHaveBeenCalledWith("rule", RULE_ID, undefined);
    await userEvent.click(control()!);
    expect(await screen.findByLabelText("Start (UTC)")).toHaveValue("2026-11-09 08:00");
    expect(screen.getByLabelText("End (UTC, exclusive)")).toHaveValue("2026-11-09 11:00");
    expect(screen.getByText(/from this rule's storm baseline/)).toBeInTheDocument();
    expect(listHeading()).toBeNull();
  });

  it("a silence names the silence baseline", async () => {
    vi.spyOn(ruleMatchApi, "anomalies").mockResolvedValue(block({ span: span({ window_seconds: 3600 }) }));
    await renderFor(as(ruleDetail({ ...RES, anomaly_type: "RULE_SILENCE" })));
    await userEvent.click(control()!);
    expect(screen.getByText(/from this rule's silence baseline/)).toBeInTheDocument();
  });

  it("says the window width could not be read, and offers no button", async () => {
    vi.spyOn(ruleMatchApi, "anomalies").mockRejectedValue(new ApiError(500, "down"));
    await renderFor(as(ruleDetail(RES)));
    expect(control()).toBeNull();
    expect(screen.getByText(/match-window width of this rule could not be read/)).toBeInTheDocument();
  });

  it("an open rule incident makes no exclusion read at all", async () => {
    const windows = vi.spyOn(ruleMatchApi, "anomalies");
    await renderFor(as(ruleDetail()));
    expect(forSubject).not.toHaveBeenCalled();
    expect(windows).not.toHaveBeenCalled();
    expect(screen.getByText("Available once the anomaly is resolved.")).toBeInTheDocument();
  });
});
