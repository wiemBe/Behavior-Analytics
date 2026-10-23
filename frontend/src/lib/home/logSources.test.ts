import { describe, expect, it } from "vitest";

import { behavior, summary } from "./fixtures";
import { logSources, SILENT_DEFINITION } from "./logSources";

const src = (state: Parameters<typeof behavior>[0] extends infer P ? P : never) => behavior(state);

describe("logSources", () => {
  it("states n of m judged normal, judged excluding INSUFFICIENT_DATA", () => {
    const r = logSources(
      summary({ monitored_sources: 6, silent_sources: 1 }),
      [
        src({ state: "NORMAL" }),
        src({ state: "RESOLVED" }),
        src({ state: "OPEN" }),
        src({ state: "INSUFFICIENT_DATA" }),
        src({ state: "INSUFFICIENT_DATA" }),
        src({ state: "RECOVERING" }),
      ],
      59,
    );
    expect(r.judged).toBe(4);
    expect(r.normal).toBe(2);
    expect(r.judgedText).toBe("2 of 4 judged normal");
    expect(r.notJudged).toBe(2);
    expect(r.total).toBe(59);
    expect(r.monitored).toBe(6);
    expect(r.qualifiers).toEqual([]);
  });

  it("reuses the health groups, with INSUFFICIENT_DATA neutral and apart from Normal", () => {
    const r = logSources(summary(), [src({ state: "NORMAL" }), src({ state: "INSUFFICIENT_DATA" })], null);
    expect(r.groups.map((g) => [g.key, g.count, g.tone])).toEqual([
      ["insufficient", 1, ""],
      ["normal", 1, "ok"],
    ]);
  });

  it("takes the silent count from the summary and names its definition", () => {
    const r = logSources(summary({ silent_sources: 3 }), [src({ state: "NORMAL" })], null);
    expect(r.silent).toBe(3);
    expect(r.silentDefinition).toBe(SILENT_DEFINITION);
    expect(r.silentDefinition).toMatch(/NO_EVENTS/);
  });

  it("is INSUFFICIENT_DATA with no normal count when nothing is judged", () => {
    const r = logSources(summary(), [src({ state: "INSUFFICIENT_DATA" })], 10);
    expect(r.judged).toBe(0);
    expect(r.normal).toBeNull();
    expect(r.judgedText).toBeNull();
    expect(r.qualifiers.map((q) => q.kind)).toEqual(["INSUFFICIENT_DATA"]);
  });

  it("is INSUFFICIENT_DATA for no sources, with an unknown total kept null", () => {
    const r = logSources(summary(), [], null);
    expect(r.normal).toBeNull();
    expect(r.total).toBeNull();
    expect(r.qualifiers[0]).toMatchObject({ kind: "INSUFFICIENT_DATA" });
  });

  it("carries the evaluation-fault qualifier with its counts and keeps the drill-down to sources", () => {
    const r = logSources(
      summary({
        monitored_sources: 6,
        sources_with_evaluation_faults: 2,
        buckets_not_judged: 1,
      }),
      [src({ state: "NORMAL" }), src({ state: "OPEN" })],
      59,
    );
    expect(r.evaluationFault).toBe("Not judged: 2 sources (24 h)");
    expect(r.evaluationFaultDetail).toBe(
      "2 sources have an evaluation fault; 1 bucket was not judged in 24 h",
    );
    expect(r.judgedText).toBe("1 of 2 judged normal");
  });
});
