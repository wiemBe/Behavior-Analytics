import { describe, expect, it } from "vitest";

import { analytics, offenseRow, page } from "./fixtures";
import { offensesCard } from "./offenses";

const snapshotExists = page([offenseRow()], 12);

describe("offensesCard", () => {
  it("reports open, critical by the product threshold, and unassigned", () => {
    const r = offensesCard(
      analytics(
        { active: 10, critical: 3, unassigned: 4, exceeding_sla: 2, critical_magnitude: 8 },
        [
          { key: "9", count: 3 },
          { key: "5", count: 7 },
        ],
      ),
      snapshotExists,
    );
    expect(r).toMatchObject({ open: 10, critical: 3, unassigned: 4, exceedingSla: 2 });
    expect(r.criticalMagnitude).toBe(8);
    expect(r.breakdown).toEqual({ critical: 3, below: 7, unknown: 0 });
    expect(r.qualifiers).toEqual([]);
  });

  it("is NOT_COLLECTED, with no zeros, when the probe shows no snapshot at all", () => {
    const r = offensesCard(analytics({ active: 0 }), page([], 0));
    expect(r.open).toBeNull();
    expect(r.critical).toBeNull();
    expect(r.unassigned).toBeNull();
    expect(r.exceedingSla).toBeNull();
    expect(r.breakdown).toBeNull();
    expect(r.qualifiers).toEqual([
      { kind: "NOT_COLLECTED", reason: "No offense snapshot has been collected yet." },
    ]);
  });

  it("shows a real zero when snapshots exist and none is open", () => {
    const r = offensesCard(analytics({ active: 0 }), snapshotExists);
    expect(r.open).toBe(0);
    expect(r.critical).toBe(0);
    expect(r.qualifiers).toEqual([]);
  });

  it("does not trust zeros it cannot confirm: no probe and nothing open is NOT_COLLECTED", () => {
    const r = offensesCard(analytics({ active: 0 }), null);
    expect(r.open).toBeNull();
    expect(r.qualifiers[0].kind).toBe("NOT_COLLECTED");
  });

  it("shows the analytics as they are when open offenses exist and the probe failed", () => {
    const r = offensesCard(analytics({ active: 5, critical: 1 }), null);
    expect(r.open).toBe(5);
    expect(r.qualifiers).toEqual([]);
  });

  it("counts a non-numeric magnitude key as unknown, not as below critical", () => {
    const r = offensesCard(
      analytics({ active: 10, critical: 2 }, [
        { key: "8", count: 2 },
        { key: "unassigned", count: 3 },
        { key: "4", count: 5 },
      ]),
      snapshotExists,
    );
    expect(r.breakdown).toEqual({ critical: 2, below: 5, unknown: 3 });
  });

  it("gives no 'below' figure when the parts exceed the total", () => {
    const r = offensesCard(
      analytics({ active: 3, critical: 2 }, [{ key: "x", count: 5 }]),
      snapshotExists,
    );
    expect(r.breakdown?.below).toBeNull();
  });

  it("is NOT_COLLECTED from a null count even when the probe still lists rows", () => {
    const r = offensesCard(
      analytics({ active: null }),
      snapshotExists,
    );
    expect(r.open).toBeNull();
    expect(r.critical).toBeNull();
    expect(r.unassigned).toBeNull();
    expect(r.exceedingSla).toBeNull();
    expect(r.breakdown).toBeNull();
    expect(r.qualifiers).toEqual([
      { kind: "NOT_COLLECTED", reason: "No offense snapshot has been collected yet." },
    ]);
  });

  it("is NOT_COLLECTED from a null count while the probe has not arrived", () => {
    const r = offensesCard(analytics({ active: null }), null);
    expect(r.open).toBeNull();
    expect(r.qualifiers).toEqual([
      { kind: "NOT_COLLECTED", reason: "No offense snapshot has been collected yet." },
    ]);
  });
});

describe("offensesCard unknown counts", () => {
  it("takes the measured counts, and the measured magnitude count wins over the distribution", () => {
    const r = offensesCard(
      analytics({ active: 10, critical: 2, critical_unknown: 4, age_unknown: 1 }, [
        { key: "unassigned", count: 3 },
      ]),
      snapshotExists,
    );
    expect(r.criticalUnknown).toBe(4);
    expect(r.ageUnknown).toBe(1);
    expect(r.breakdown).toEqual({ critical: 2, below: 4, unknown: 4 });
  });

  it("leaves both null when the backend sends none, and for no snapshot", () => {
    const r = offensesCard(analytics({ active: 10, critical: 2 }), snapshotExists);
    expect(r.criticalUnknown).toBeNull();
    expect(r.ageUnknown).toBeNull();
    const none = offensesCard(analytics({ active: null, critical_unknown: 3 }), snapshotExists);
    expect(none.criticalUnknown).toBeNull();
    expect(none.ageUnknown).toBeNull();
  });
});
