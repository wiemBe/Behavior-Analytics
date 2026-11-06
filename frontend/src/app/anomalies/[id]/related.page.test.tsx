// SOC-3b: every family's anomaly page carries the Related activity section, the pad in the URL
// reaches the read, a failed read never breaks the investigation, and the SOC-1b triage block
// still renders beside it.

import { act, render, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomalyDetailPage from "./page";
import { ipsDetail } from "@/components/ips/fixtures";
import { pwDetail } from "@/components/propertywatch/fixtures";
import { wafDetail } from "@/components/waf/fixtures";
import {
  ApiError,
  api,
  type AnomalyDetail,
  type RelatedActivity,
  type RelatedFamily,
  type Triage,
} from "@/lib/api";

vi.mock("@/components/behavior/VolumeChart", () => ({ VolumeChart: () => <div /> }));
vi.mock("@/components/waf/WafRequestChart", () => ({ WafRequestChart: () => <div /> }));
vi.mock("@/components/ips/IpsSeriesChart", () => ({ IpsSeriesChart: () => <div /> }));
vi.mock("@/components/propertywatch/PropertyWatchSeriesChart", () => ({
  PropertyWatchSeriesChart: () => <div />,
}));

function phaseA(): AnomalyDetail {
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
  } as AnomalyDetail;
}

function related(family: RelatedFamily, id: string, pad: "1h" | "2h" = "2h"): RelatedActivity {
  const empty = { state: "OK", reason_code: null, total: 0, items: [] } as const;
  return {
    state: "OK",
    reason_code: null,
    anchor: {
      family,
      anomaly_id: id,
      log_source_id: "s-1",
      qradar_log_source_id: 9001,
      log_source_name: "LAB Firewall",
      window_start: "2026-11-05T16:30:00Z",
      window_end: "2026-11-05T20:30:00Z",
      pad,
    },
    offenses: {
      state: "OK",
      reason_code: null,
      total: 1,
      items: [
        {
          qradar_offense_id: 1501,
          description: "Port scan from a internal host",
          status: "OPEN",
          magnitude: 7,
          severity: 6,
          start_time: "2026-11-05T18:12:00Z",
          last_updated_time: "2026-11-05T18:58:00Z",
          close_time: null,
          offense_type_name: null,
          rule_count: 1,
          captured_at: "2026-11-05T19:00:00Z",
        },
      ],
    },
    anomalies: { ...empty, items: [] },
    rules: { ...empty, items: [] },
  };
}

const triageRead = (family: string, id: string): Triage =>
  ({
    family,
    anomaly_id: id,
    status: "IN_PROGRESS",
    owner: "k.osei",
    disposition: null,
    disposition_note: null,
    updated_by: "k.osei",
    updated_at: "2026-11-05T18:50:00Z",
    version: 1,
    events: { items: [], total: 0, limit: 50, offset: 0 },
  }) as Triage;

let relatedRead: ReturnType<typeof vi.spyOn>;

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

async function renderFor(
  detail: AnomalyDetail,
  family: RelatedFamily,
  searchParams?: { pad?: string | string[] },
) {
  vi.spyOn(api, "anomaly").mockResolvedValue(detail);
  vi.spyOn(api, "triage").mockResolvedValue(triageRead(family, detail.id));
  relatedRead = vi
    .spyOn(api, "relatedActivity")
    .mockImplementation(async (f, id, pad) => related(f, id, pad === "1h" ? "1h" : "2h"));
  await mount(
    await AnomalyDetailPage({
      params: Promise.resolve({ id: detail.id }),
      searchParams: searchParams ? Promise.resolve(searchParams) : undefined,
    }),
  );
}

/** The section suspends on its read, so the first render is awaited inside act. */
async function mount(page: ReactElement) {
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(page);
  });
  return view;
}

describe("each family's detail renders the Related activity section", () => {
  const cases: Array<[string, () => AnomalyDetail, RelatedFamily]> = [
    ["Phase A (log source)", () => phaseA(), "log_source"],
    ["WAF endpoint", () => wafDetail() as unknown as AnomalyDetail, "waf"],
    ["IPS entity", () => ipsDetail() as unknown as AnomalyDetail, "ips"],
    ["property-watch value", () => pwDetail() as unknown as AnomalyDetail, "property_watch"],
  ];

  it.each(cases)("%s", async (_name, make, family) => {
    const detail = make();
    await renderFor(detail, family);
    expect(relatedRead).toHaveBeenCalledWith(family, detail.id, "2h");
    const heading = await screen.findByRole("heading", {
      level: 2,
      name: "Related activity, 16:30–20:30 UTC (±2 h)",
    });
    const section = heading.closest("section")!;
    expect(within(section).getByRole("link", { name: "Open offense 1501" })).toHaveAttribute(
      "href",
      "/offenses/1501",
    );
    // The SOC-1b triage block is still on the page, before this section.
    const triage = screen.getByRole("heading", { level: 2, name: "Triage" });
    expect(
      triage.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Take it" })).toBeInTheDocument();
  });
});

describe("where the section sits", () => {
  it("comes after the timeline and the evidence, before the investigation detail", async () => {
    await renderFor(phaseA(), "log_source");
    await screen.findByRole("heading", { level: 2, name: /^Related activity, / });
    expect(
      screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent?.replace(/,.*/, "")),
    ).toEqual(["Triage", "Timeline", "What changed", "Related activity", "Investigation detail"]);
  });
});

describe("the pad in the URL", () => {
  it("reaches the read and marks the control", async () => {
    await renderFor(phaseA(), "log_source", { pad: "1h" });
    expect(relatedRead).toHaveBeenCalledWith("log_source", "a-1", "1h");
    await screen.findByRole("heading", { level: 2, name: /\(±1 h\)$/ });
    expect(screen.getByRole("link", { name: "1 h" })).toHaveAttribute("aria-current", "page");
  });

  it("an unknown pad falls back to 2 h instead of failing", async () => {
    await renderFor(phaseA(), "log_source", { pad: "99h" });
    expect(relatedRead).toHaveBeenCalledWith("log_source", "a-1", "2h");
  });
});

describe("failures never break the investigation", () => {
  it("a failed related read shows its own error and keeps the page and the triage block", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(phaseA());
    vi.spyOn(api, "triage").mockResolvedValue(triageRead("log_source", "a-1"));
    vi.spyOn(api, "relatedActivity").mockRejectedValue(new ApiError(500, "boom"));
    const { container } = await mount(
      await AnomalyDetailPage({ params: Promise.resolve({ id: "a-1" }) }),
    );
    expect(await screen.findByText(/related activity could not be loaded/i)).toBeInTheDocument();
    expect(container.querySelector("h1")).toHaveTextContent("LAB Firewall");
    expect(screen.getByRole("button", { name: "Take it" })).toBeInTheDocument();
  });

  it("a 403 names the permission", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(phaseA());
    vi.spyOn(api, "triage").mockResolvedValue(triageRead("log_source", "a-1"));
    vi.spyOn(api, "relatedActivity").mockRejectedValue(new ApiError(403, "no"));
    await mount(await AnomalyDetailPage({ params: Promise.resolve({ id: "a-1" }) }));
    expect(await screen.findByText(/needs the read:anomalies permission/)).toBeInTheDocument();
  });

  it("a custom-rule breach has no related activity and is not read", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue({ ...phaseA(), subject_kind: "CUSTOM_RULE" } as never);
    const read = vi.spyOn(api, "relatedActivity");
    await mount(await AnomalyDetailPage({ params: Promise.resolve({ id: "a-1" }) }));
    expect(read).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: /Related activity/ })).not.toBeInTheDocument();
  });
});
