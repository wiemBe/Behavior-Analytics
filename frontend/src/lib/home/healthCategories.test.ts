import { describe, expect, it } from "vitest";

import type { SocOverview } from "@/lib/api";

import { fleet, summary } from "./fixtures";
import { healthCategories } from "./healthCategories";

const overview = { log_sources: { in_maintenance: 2 } } as SocOverview;

describe("healthCategories", () => {
  it("lists only categories with detection logic, and never Degraded", () => {
    const rows = healthCategories({ summary: summary(), fleet: fleet([]), overview });
    expect(rows.map((r) => r.key)).toEqual([
      "silent",
      "exceeding",
      "new",
      "conflict",
      "unclassified",
      "maintenance",
    ]);
    expect(rows.map((r) => r.label.toLowerCase()).join(" ")).not.toContain("degraded");
  });

  it("reads each count from the response that owns it", () => {
    const rows = healthCategories({
      summary: summary({ silent_sources: 3, spikes: 5 }),
      fleet: fleet([], { new_sources: 7, conflict_sources: 1, unclassified_sources: 2 }),
      overview,
    });
    expect(rows.map((r) => r.count)).toEqual([3, 5, 7, 1, 2, 2]);
  });

  it("keeps a measured zero as 0 and an unread figure as null with its reason", () => {
    const rows = healthCategories({ summary: summary(), fleet: null, overview: null });
    const by = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(by.silent.count).toBe(0);
    expect(by.silent.gap).toBeNull();
    expect(by.new.count).toBeNull();
    expect(by.new.gap).toMatch(/Fleet/);
    expect(by.maintenance.count).toBeNull();
    expect(by.maintenance.gap).toMatch(/Overview/);
  });

  it("links a category to the page that owns it, and none where no page does", () => {
    const rows = healthCategories({ summary: summary(), fleet: fleet([]), overview });
    const by = Object.fromEntries(rows.map((r) => [r.key, r.href]));
    expect(by.new).toBe("/fleet/sources?review_status=NEW");
    expect(by.silent).toBe(
      "/anomalies?anomaly_type=NO_EVENTS&active_only=true",
    );
    expect(by.exceeding).toBe(
      "/anomalies?anomaly_type=VOLUME_SPIKE&active_only=true",
    );
    expect(by.maintenance).toBeNull();
  });
});
