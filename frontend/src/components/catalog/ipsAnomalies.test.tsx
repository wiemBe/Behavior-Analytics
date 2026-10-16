// Recent IPS anomalies (UI-3.5b): the merge of the two bounded reads, and the table.

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { failed, forbidden, loading, ok } from "@/components/home/testkit";
import { ipsListItem } from "@/components/ips/fixtures";
import { homeReads } from "@/lib/api";
import { page } from "@/lib/home/fixtures";

import { IPS_ANOMALIES_LIMIT, mergeIpsAnomalies } from "./ipsAnomalies";
import { RecentIpsAnomalies } from "./RecentIpsAnomalies";

const res = vi.hoisted(() => ({ byKey: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => res.byKey[key ?? ""] ?? { status: "loading" },
}));

const ENTITY = homeReads.activeIpsAnomalies("IPS_ENTITY", IPS_ANOMALIES_LIMIT).key;
const SOURCE = homeReads.activeIpsAnomalies("IPS_SOURCE", IPS_ANOMALIES_LIMIT).key;

const entity = (id: string, detected_at: string) => ipsListItem({ id, detected_at });
const source = (id: string, detected_at: string) =>
  ipsListItem({
    id,
    detected_at,
    subject_kind: "IPS_SOURCE",
    ips_entity: null,
    anomaly_type: "IPS_BLOCK_RATIO_SHIFT",
    observed_value: 0.42,
    expected_value: 0.05,
    deviation_ratio: null,
  });

beforeEach(() => {
  res.byKey = {};
});

describe("mergeIpsAnomalies", () => {
  it("orders the union newest first, whichever list a row came from", () => {
    const view = mergeIpsAnomalies(
      page([entity("e1", "2026-10-12T09:00:00Z"), entity("e2", "2026-10-12T07:00:00Z")]),
      page([source("s1", "2026-10-12T08:00:00Z"), source("s2", "2026-10-12T10:00:00Z")]),
    );
    expect(view.rows.map((r) => r.id)).toEqual(["s2", "e1", "s1", "e2"]);
  });

  it("cuts to the limit and never drops the newest row", () => {
    const entities = Array.from({ length: 5 }, (_, i) =>
      entity(`e${i}`, `2026-10-12T0${i}:00:00Z`),
    );
    const sources = Array.from({ length: 5 }, (_, i) =>
      source(`s${i}`, `2026-10-12T1${i}:30:00Z`),
    );
    const view = mergeIpsAnomalies(page(entities, 40), page(sources, 3));
    expect(view.rows).toHaveLength(IPS_ANOMALIES_LIMIT);
    // The five newest of the ten are all source-level rows, 14:30 down to 10:30.
    expect(view.rows.map((r) => r.id)).toEqual(["s4", "s3", "s2", "s1", "s0"]);
    expect(view.rows[0].detectedAt).toBe("2026-10-12T14:30:00Z");
  });

  it("is 'n of N' from the two totals", () => {
    const view = mergeIpsAnomalies(
      page([entity("e1", "2026-10-12T09:00:00Z")], 14),
      page([source("s1", "2026-10-12T08:00:00Z")], 4),
    );
    expect(view.total).toBe(18);
    expect(view.badge).toBe("18 active");
    expect(view.shown).toBe("Showing the latest 2 of 18");
  });

  it("says nothing about 'n of N' when every active row is listed", () => {
    const view = mergeIpsAnomalies(page([entity("e1", "2026-10-12T09:00:00Z")]), page([]));
    expect(view.shown).toBeNull();
    expect(view.badge).toBe("1 active");
  });

  it("breaks a tie on the id, not on which list came first", () => {
    const t = "2026-10-12T09:00:00Z";
    const a = mergeIpsAnomalies(page([entity("b", t)]), page([source("a", t)]));
    const b = mergeIpsAnomalies(page([source("a", t)]), page([entity("b", t)]));
    expect(a.rows.map((r) => r.id)).toEqual(["a", "b"]);
    expect(b.rows.map((r) => r.id)).toEqual(["a", "b"]);
  });
});

describe("RecentIpsAnomalies", () => {
  const set = (entities: unknown, sources: unknown) => (res.byKey = { [ENTITY]: entities, [SOURCE]: sources });

  it("reads both kinds, active only, with the limit the other recent lists use", () => {
    expect(ENTITY).toBe("/anomalies?subject_kind=IPS_ENTITY&active_only=true&limit=5");
    expect(SOURCE).toBe("/anomalies?subject_kind=IPS_SOURCE&active_only=true&limit=5");
  });

  it("writes each row in its detector's unit and never says EPS", () => {
    set(
      ok(page([entity("e1", "2026-10-12T09:00:00Z")], 3)),
      ok(page([source("s1", "2026-10-12T08:00:00Z")], 2)),
    );
    const { container } = render(<RecentIpsAnomalies />);
    const rows = screen.getAllByRole("link", { name: /^Investigate/ }).map((a) => a.closest("tr")!);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Destination port 445/tcp")).toBeInTheDocument();
    expect(rows[0]).toHaveTextContent("940 → 120");
    expect(rows[0]).toHaveTextContent("events / bucket");
    expect(rows[0]).toHaveTextContent("Destination-port rate spike · lab-ips-01");
    expect(rows[1]).toHaveTextContent("Source-level");
    expect(rows[1]).toHaveTextContent("42.0% → 5.0%");
    expect(container.textContent).not.toMatch(/EPS|volume/i);
    expect(container.textContent).not.toMatch(/IPS_[A-Z_]+/);
  });

  it("shows the lifecycle state in upper case, beside the upper-case severity", () => {
    set(ok(page([ipsListItem({ state: "OPEN", severity: "HIGH" })])), ok(page([])));
    const row = (render(<RecentIpsAnomalies />), screen.getByRole("link", { name: /^Investigate/ }).closest("tr")!);
    expect(within(row).getAllByText("OPEN").length).toBeGreaterThan(0);
    expect(within(row).queryByText("Open")).toBeNull();
  });

  it("a detector this build does not know shows no figure, not a guessed unit", () => {
    set(ok(page([ipsListItem({ anomaly_type: "IPS_FUTURE_THING" as never })])), ok(page([])));
    const { container } = render(<RecentIpsAnomalies />);
    const row = screen.getByRole("link", { name: /^Investigate/ }).closest("tr")!;
    expect(row).toHaveTextContent("—");
    expect(row).not.toHaveTextContent("940");
    expect(container.textContent).not.toMatch(/EPS/);
  });

  it("badges 'n active' from both totals and says 'n of N' beneath", () => {
    set(
      ok(page([entity("e1", "2026-10-12T09:00:00Z")], 14)),
      ok(page([source("s1", "2026-10-12T08:00:00Z")], 4)),
    );
    render(<RecentIpsAnomalies />);
    expect(screen.getByText("18 active")).toBeInTheDocument();
    expect(screen.getByText(/Showing the latest 2 of 18\./)).toBeInTheDocument();
  });

  it("links to the entity list and to the source-level list", () => {
    set(ok(page([entity("e1", "2026-10-12T09:00:00Z")])), ok(page([])));
    render(<RecentIpsAnomalies />);
    expect(screen.getByRole("link", { name: "View active IPS entity anomalies" })).toHaveAttribute(
      "href",
      "/anomalies?subject_kind=IPS_ENTITY&active_only=true",
    );
    expect(screen.getByRole("link", { name: "Source-level anomalies" })).toHaveAttribute(
      "href",
      "/anomalies?subject_kind=IPS_SOURCE&active_only=true",
    );
  });

  it("an empty list is not a clean bill", () => {
    set(ok(page([])), ok(page([])));
    render(<RecentIpsAnomalies />);
    expect(screen.getByText(/not a clean bill/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("one failed read fails the widget: it will not claim the latest of a half union", () => {
    set(ok(page([entity("e1", "2026-10-12T09:00:00Z")])), failed(500));
    render(<RecentIpsAnomalies />);
    expect(screen.getByRole("alert")).toHaveTextContent("could not be read");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("is loading, then names the permission when refused", () => {
    set(loading, ok(page([])));
    const { unmount } = render(<RecentIpsAnomalies />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Loading/);
    unmount();
    set(forbidden, ok(page([])));
    render(<RecentIpsAnomalies />);
    expect(screen.getByRole("status")).toHaveTextContent("permission read:anomalies");
  });
});
