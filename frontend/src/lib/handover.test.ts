import { describe, expect, it } from "vitest";

import { cutoff, noteProblem, NOTE_MAX, rowFlags, snapshotIncomplete, sourceLines, splitItems } from "./handover";

const src = (kind: string, state: string) => ({
  kind,
  state,
  count: state === "OK" ? 3 : null,
  new_count: null,
  permission: null,
  as_of: null,
  reason_code: null,
});

describe("rowFlags", () => {
  it("names Unassigned, Over SLA and New in reading order", () => {
    expect(rowFlags({ kind: "alert", owner: null, over_sla: true, change: "NEW" })).toEqual([
      "unassigned",
      "overSla",
      "new",
    ]);
  });

  it("says nothing about the SLA when it is unknown", () => {
    expect(rowFlags({ kind: "offense", owner: "ada", over_sla: null, change: null })).toEqual([]);
    expect(rowFlags({ kind: "offense", owner: "ada", over_sla: false, change: "UPDATED" })).toEqual(["updated"]);
  });

  it("does not call a source review or a catch-all sender unassigned", () => {
    expect(rowFlags({ kind: "source_review", owner: null, over_sla: null, change: null })).toEqual([]);
    expect(rowFlags({ kind: "catchall_sender", owner: null, over_sla: null, change: null })).toEqual([]);
  });
});

describe("splitItems", () => {
  it("puts a watched row in Watch only, so it appears once", () => {
    const items = [
      { id: "a", watch: true },
      { id: "b", watch: false },
    ] as never;
    const out = splitItems({ items });
    expect(out.watch.map((i) => i.id)).toEqual(["a"]);
    expect(out.others.map((i) => i.id)).toEqual(["b"]);
  });
});

describe("sourceLines", () => {
  it("lists every source that is not OK, open and acted-on, and nothing else", () => {
    const lines = sourceLines({
      sources: [src("anomaly", "OK"), src("offense", "FORBIDDEN")] as never,
      acted_on_sources: [
        { kind: "triage", state: "FAILED", count: null, permission: null, reason_code: "LOAD_FAILED" },
        { kind: "alert", state: "OK", count: 0, permission: null, reason_code: null },
      ],
    });
    expect(lines.map((l) => `${l.scope}:${l.source.kind}:${l.source.state}`)).toEqual([
      "open:offense:FORBIDDEN",
      "acted:triage:FAILED",
    ]);
  });

  it("makes the snapshot incomplete for a FAILED acted-on source", () => {
    expect(
      snapshotIncomplete({
        sources: [src("anomaly", "OK")] as never,
        acted_on_sources: [{ kind: "alert", state: "FAILED", count: null, permission: null, reason_code: "LOAD_FAILED" }],
      }),
    ).toBe(true);
    expect(snapshotIncomplete({ sources: [src("anomaly", "OK")] as never, acted_on_sources: [] })).toBe(false);
  });
});

describe("cutoff", () => {
  const body = { items: new Array(50).fill({}) as never, total: 120 };
  it("is exact, a lower bound, or none", () => {
    expect(cutoff({ ...body, truncated: true, total_exact: true })).toEqual({ kind: "exact", shown: 50, total: 120 });
    expect(cutoff({ ...body, truncated: true, total_exact: false })).toEqual({ kind: "atLeast", shown: 50, total: 120 });
    expect(cutoff({ ...body, truncated: false, total_exact: true })).toEqual({ kind: "none" });
  });
});

describe("noteProblem", () => {
  it("refuses an empty or blank note and a note over the limit", () => {
    expect(noteProblem("")).toBe("required");
    expect(noteProblem("   \n")).toBe("required");
    expect(noteProblem("x".repeat(NOTE_MAX))).toBeNull();
    expect(noteProblem("x".repeat(NOTE_MAX + 1))).toBe("tooLong");
  });
});
