import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ipsListItem } from "@/components/ips/fixtures";
import { homeReads } from "@/lib/api";
import { page, summary } from "@/lib/home/fixtures";

import { ok } from "./testkit";
import { RecentAnomalies } from "./RecentAnomalies";

const res = vi.hoisted(() => ({ byKey: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => res.byKey[key ?? ""] ?? { status: "loading" },
}));

const LIST = homeReads.activeAnomalies(5).key;
const SUM = homeReads.anomalySummary().key;

beforeEach(() => {
  res.byKey = {};
});

describe("RecentAnomalies with an IPS row", () => {
  it("names the entity, the detector and the IPS source, with no EPS or volume wording", () => {
    res.byKey = { [LIST]: ok(page([ipsListItem()])), [SUM]: ok(summary()) };
    render(<RecentAnomalies />);
    const row = screen.getByRole("link", { name: /Investigate/ }).closest("tr")!;
    expect(within(row).getByText("Destination port 445/tcp")).toBeInTheDocument();
    expect(row).toHaveTextContent("Destination-port rate spike · lab-ips-01");
    expect(row.textContent).not.toMatch(/EPS|volume/i);
    expect(row).not.toHaveTextContent("IPS_PORT_RATE_SPIKE");
  });

  it("links the subject to its investigation, not to the log-source EPS page", () => {
    res.byKey = { [LIST]: ok(page([ipsListItem({ id: "i9" })])), [SUM]: ok(summary()) };
    render(<RecentAnomalies />);
    expect(screen.getByRole("link", { name: "Destination port 445/tcp" })).toHaveAttribute(
      "href",
      "/anomalies/i9",
    );
    expect(document.querySelector('a[href^="/behavior/sources/"]')).toBeNull();
  });

  it("names a source-level row Source-level and writes a null ratio as a dash", () => {
    res.byKey = {
      [LIST]: ok(
        page([ipsListItem({ subject_kind: "IPS_SOURCE", ips_entity: null, deviation_ratio: null })]),
      ),
      [SUM]: ok(summary()),
    };
    render(<RecentAnomalies />);
    const row = screen.getByRole("link", { name: /Investigate/ }).closest("tr")!;
    expect(row).toHaveTextContent("Source-level");
    expect(within(row).getByText("—")).toBeInTheDocument();
  });
});
