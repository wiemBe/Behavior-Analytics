// SOC-13d: the Phase A anomaly page reads the source's training exclusions, hands the triage block
// its control, and shades the effective ones on the timeline with the same range in text.

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomalyDetailPage from "./page";
import { StaticSession, type SessionValue } from "@/components/auth/session";
import { CAN, READ_ONLY, exclusion, resolved } from "@/components/baseline/fixtures";
import { wafDetail } from "@/components/waf/fixtures";
import { ApiError, api, trainingExclusionApi, type AnomalyDetail, type Triage } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));
vi.mock("@/components/behavior/VolumeChart", () => ({
  VolumeChart: ({ exclusions }: { exclusions?: Array<{ start: string; end: string }> }) => (
    <div data-testid="chart" data-exclusions={JSON.stringify(exclusions ?? [])} />
  ),
}));
vi.mock("@/components/waf/WafRequestChart", () => ({ WafRequestChart: () => <div /> }));

const triage = (id: string): Triage => ({
  family: "log_source",
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

let forSource: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "anomalyPivot").mockRejectedValue(new ApiError(404, "no pivot"));
  vi.spyOn(api, "caller").mockResolvedValue({
    subject: "k.osei",
    permissions: [],
    effective_permissions: ["read:anomalies", "anomaly:triage"],
  } as unknown as Awaited<ReturnType<typeof api.caller>>);
  vi.spyOn(api, "alerts").mockResolvedValue({ items: [], total: 0, limit: 500, offset: 0 });
  vi.spyOn(api, "sourceMetrics").mockResolvedValue([
    {
      bucket_start: "2026-11-04T08:00:00Z",
      bucket_seconds: 300,
      event_count: 600,
      average_eps: 2,
      peak_eps: null,
      completeness: "COMPLETE",
      last_event_at: null,
    },
  ]);
  vi.spyOn(api, "wafEndpointMetrics").mockResolvedValue({ items: [] } as never);
  vi.spyOn(api, "wafEndpointBaselines").mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
  forSource = vi
    .spyOn(trainingExclusionApi, "forSource")
    .mockResolvedValue({ items: [exclusion()], limit: 100 });
});

async function renderFor(detail: AnomalyDetail, value: SessionValue = CAN) {
  vi.spyOn(api, "anomaly").mockResolvedValue(detail);
  vi.spyOn(api, "triage").mockResolvedValue(triage(detail.id));
  return render(
    <StaticSession value={value}>
      {await AnomalyDetailPage({ params: Promise.resolve({ id: detail.id }) })}
    </StaticSession>,
  );
}

const control = () => screen.queryByRole("button", { name: "Exclude interval from training" });

describe("the control in the triage block", () => {
  it("is offered inside the triage section for a resolved anomaly and a caller with anomaly:triage", async () => {
    await renderFor(resolved());
    const triageSection = screen.getByRole("heading", { name: "Triage" }).closest("section")!;
    expect(within(triageSection).getByRole("button", { name: "Exclude interval from training" })).toBeInTheDocument();
    expect(forSource).toHaveBeenCalledWith("11111111-1111-1111-1111-111111111111");
  });

  it("reads the bucket width from the stored buckets: the form opens on the aligned span", async () => {
    await renderFor(resolved());
    control()!.click();
    expect(await screen.findByLabelText("Start (UTC)")).toHaveValue("2026-11-04 08:05");
    expect(screen.getByLabelText("End (UTC, exclusive)")).toHaveValue("2026-11-04 09:55");
  });

  it("gives a read-only caller the protection line but no control", async () => {
    await renderFor(resolved(), READ_ONLY);
    expect(control()).toBeNull();
    expect(screen.getByText(/Training protection/)).toBeInTheDocument();
  });

  it("says the exclusion is not yet available for an anomaly that is still open", async () => {
    await renderFor(resolved({ state: "OPEN", resolved_at: null }));
    expect(control()).toBeNull();
    expect(screen.getByText("Available once the anomaly is resolved.")).toBeInTheDocument();
  });

  // SOC-13f: a resolved WAF rate anomaly now carries the control (exclusion.family.page.test.tsx);
  // a novelty detector has no band, so this page still says nothing about training for it.
  it("shows nothing about training for a detector with no baseline band", async () => {
    await renderFor(
      wafDetail({ state: "RESOLVED", anomaly_type: "WAF_NEW_ENDPOINT_ACCESS" }) as unknown as AnomalyDetail,
    );
    expect(control()).toBeNull();
    expect(screen.queryByText("Baseline training")).toBeNull();
    expect(forSource).not.toHaveBeenCalled();
  });

  it("does not hide the page or claim there are none when the exclusions cannot be read", async () => {
    forSource.mockRejectedValue(new ApiError(500, "boom"));
    const { container } = await renderFor(resolved());
    expect(container.querySelector("h1")).toHaveTextContent("LAB Firewall");
    expect(screen.getByText("Analyst exclusions could not be loaded for this incident.")).toBeInTheDocument();
    expect(screen.getByTestId("chart")).toHaveAttribute("data-exclusions", "[]");
  });

  it("names read:anomalies when the caller may not read the exclusions", async () => {
    forSource.mockRejectedValue(new ApiError(403, "forbidden"));
    await renderFor(resolved());
    expect(screen.getByText(/Reading analyst exclusions needs the read:anomalies permission/)).toBeInTheDocument();
  });
});

describe("the timeline", () => {
  it("hands the chart each effective exclusion and writes the same UTC range beside it", async () => {
    forSource.mockResolvedValue({
      items: [
        exclusion(),
        exclusion({ id: "r", state: "REVOKED", starts_at: "2026-11-02T00:00:00Z", ends_at: "2026-11-02T01:00:00Z" }),
      ],
      limit: 100,
    });
    await renderFor(resolved());
    expect(JSON.parse(screen.getByTestId("chart").getAttribute("data-exclusions")!)).toEqual([
      { start: "2026-11-04T08:00:00Z", end: "2026-11-04T10:00:00Z" },
    ]);
    expect(screen.getByText(/Excluded from baseline training: 04 Nov 08:00–10:00 UTC \(Pending next baseline rebuild\)/)).toBeInTheDocument();
  });

  it("draws nothing extra when there are none", async () => {
    forSource.mockResolvedValue({ items: [], limit: 100 });
    await renderFor(resolved());
    expect(screen.getByTestId("chart")).toHaveAttribute("data-exclusions", "[]");
    expect(screen.queryByText(/Excluded from baseline training:/)).toBeNull();
  });
});
