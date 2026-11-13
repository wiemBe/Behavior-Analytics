// SOC-13f: the IPS source page lists the source's IPS training exclusions (scope ips:source).

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession } from "@/components/auth/session";
import { CAN, READ_ONLY, exclusion } from "@/components/baseline/fixtures";
import { IPS_BEHAVIOR_SOURCE_ID, ipsEntity, ipsSource } from "@/components/ips/behaviorFixtures";
import { ipsSourceBucket } from "@/components/ips/fixtures";
import { ApiError, api, trainingExclusionApi } from "@/lib/api";

import IpsSourcePage from "./page";

vi.mock("@/components/ips/IpsSeriesChart", () => ({ IpsSeriesChart: () => <div role="img" aria-label="chart" /> }));

const ID = IPS_BEHAVIOR_SOURCE_ID;
let forSubject: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "ipsBehaviorSource").mockResolvedValue(ipsSource());
  vi.spyOn(api, "ipsBehaviorEntities").mockResolvedValue({ items: [ipsEntity()], total: 1, limit: 50, offset: 0 });
  vi.spyOn(api, "ipsSourceMetrics").mockResolvedValue({
    items: [ipsSourceBucket({ bucket_start: "2026-10-12T09:00:00Z" })],
    total: 1,
    limit: 5000,
    offset: 0,
    range_start: "2026-10-12T08:00:00Z",
    range_end: "2026-10-13T08:00:00Z",
  });
  vi.spyOn(api, "ipsSourceBaselines").mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
  forSubject = vi.spyOn(trainingExclusionApi, "forSubject").mockResolvedValue({ items: [], limit: 100 });
});

async function renderPage(value = CAN) {
  return render(
    <StaticSession value={value}>
      {await IpsSourcePage({ params: Promise.resolve({ id: ID }), searchParams: Promise.resolve({}) })}
    </StaticSession>,
  );
}

describe("training exclusions on the IPS source page", () => {
  it("reads the source's ips:source exclusions and says when there are none", async () => {
    await renderPage();
    expect(forSubject).toHaveBeenCalledWith("source", ID, "ips:source");
    expect(screen.getByText(/top-source share, block ratio and port fan-out/)).toBeInTheDocument();
    expect(screen.getByText("No analyst training exclusions affect this baseline.")).toBeInTheDocument();
  });

  it("lists rows with their effect and Revoke for a caller who may triage", async () => {
    forSubject.mockResolvedValue({
      items: [exclusion({ subject_kind: "source", scope_key: "ips:source", state: "APPLIED" })],
      limit: 100,
    });
    await renderPage();
    const table = screen.getByRole("table", { name: "Analyst training exclusions" });
    expect(table).toHaveTextContent("IPS source metrics");
    expect(table).toHaveTextContent("Applied by a baseline rebuild");
    expect(screen.getByRole("button", { name: "Revoke" })).toBeInTheDocument();
  });

  it("a read-only caller sees no Revoke", async () => {
    forSubject.mockResolvedValue({
      items: [exclusion({ subject_kind: "source", scope_key: "ips:source" })],
      limit: 100,
    });
    await renderPage(READ_ONLY);
    expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
  });

  it("error is its own state, not an empty list", async () => {
    forSubject.mockRejectedValue(new ApiError(500, "boom"));
    await renderPage();
    expect(screen.getByText("Training exclusions could not be loaded.")).toBeInTheDocument();
    expect(screen.queryByText("No analyst training exclusions affect this baseline.")).toBeNull();
  });

  it("Retry reads the same subject and scope again", async () => {
    forSubject.mockRejectedValueOnce(new ApiError(500, "boom"));
    await renderPage();
    forSubject.mockClear();
    forSubject.mockResolvedValue({ items: [], limit: 100 });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(forSubject).toHaveBeenCalledWith("source", ID, "ips:source");
  });
});
