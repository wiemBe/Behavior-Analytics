import { describe, expect, it } from "vitest";

import type { QueuePage } from "@/lib/api";
import {
  PAGE_SIZE,
  QUEUE_CAP,
  compareSince,
  isNarrowed,
  markerKept,
  parseQueueQuery,
  queueApiQuery,
  queueHref,
} from "./queue";

const NOW = Date.parse("2026-11-07T12:00:00Z");

function page(states: Array<"OK" | "FAILED" | "FORBIDDEN" | "NOT_COLLECTED">): QueuePage {
  return {
    as_of: "2026-11-07T12:00:00Z",
    marker: { compare_from: "2026-11-07T06:00:00Z", kind: "STORED", marked_at: "2026-11-07T06:00:00Z" },
    sources: states.map((state, i) => ({
      kind: (["anomaly", "alert", "offense", "source_review", "catchall_sender"] as const)[i],
      state,
      count: state === "OK" ? 1 : null,
      new_count: state === "OK" ? 0 : null,
      permission: null,
      as_of: null,
      reason_code: null,
    })),
    total: 0,
    total_exact: true,
    limit: 50,
    offset: 0,
    filters_applied: { scope: "all", kind: [], family: [], severity: [], new_only: false, since: null },
    items: [],
  };
}

describe("parseQueueQuery", () => {
  it("defaults to the whole queue, first page, no comparison", () => {
    const q = parseQueueQuery({});
    expect(q).toMatchObject({ scope: "all", kinds: [], families: [], severities: [], newOnly: false, compare: "", offset: 0 });
    expect(q.ignored).toEqual([]);
  });

  it("keeps family=rule and sends it to the API (SOC-6e)", () => {
    const q = parseQueueQuery({ family: "rule" });
    expect(q.families).toEqual(["rule"]);
    expect(q.ignored).toEqual([]);
    expect(queueHref(q)).toBe("/queue?family=rule");
  });

  it("round-trips every filter through the address", () => {
    const q = parseQueueQuery({
      scope: "mine",
      kind: ["anomaly", "offense"],
      family: "waf",
      severity: ["HIGH", "CRITICAL"],
      new: "1",
      compare: "8h",
      offset: "100",
    });
    const href = queueHref(q);
    expect(href).toBe(
      "/queue?scope=mine&kind=anomaly&kind=offense&family=waf&severity=HIGH&severity=CRITICAL&new=1&compare=8h&offset=100",
    );
    const sp = new URL(href, "http://x").searchParams;
    const back = parseQueueQuery({
      scope: sp.get("scope") ?? undefined,
      kind: sp.getAll("kind"),
      family: sp.getAll("family"),
      severity: sp.getAll("severity"),
      new: sp.get("new") ?? undefined,
      compare: sp.get("compare") ?? undefined,
      offset: sp.get("offset") ?? undefined,
    });
    expect({ ...back, ignored: [] }).toEqual({ ...q, ignored: [] });
  });

  it("writes the default view as a bare path", () => {
    expect(queueHref(parseQueueQuery({}))).toBe("/queue");
  });

  it("drops what it does not know and says so", () => {
    const q = parseQueueQuery({ scope: "everyone", kind: ["alert", "bogus"], compare: "3h", offset: "-5", severity: "SEVERE" });
    expect(q.scope).toBe("all");
    expect(q.kinds).toEqual(["alert"]);
    expect(q.compare).toBe("");
    expect(q.offset).toBe(0);
    expect(q.severities).toEqual([]);
    expect(q.ignored.sort()).toEqual(["compare", "kind", "offset", "scope", "severity"]);
  });

  it("keeps the offset inside the 1000-row cap and on a page boundary", () => {
    expect(parseQueueQuery({ offset: "99999" }).offset).toBe(QUEUE_CAP - PAGE_SIZE);
    expect(parseQueueQuery({ offset: "120" }).offset).toBe(100);
  });
});

describe("queueApiQuery", () => {
  it("sends since only when given and never an empty filter", () => {
    expect(queueApiQuery(parseQueueQuery({}), undefined)).toBe("scope=all&limit=50&offset=0");
    expect(queueApiQuery(parseQueueQuery({ new: "1", kind: "alert" }), "2026-11-07T06:00:00.000Z")).toBe(
      "scope=all&kind=alert&new_only=true&since=2026-11-07T06%3A00%3A00.000Z&limit=50&offset=0",
    );
  });

  it("turns a chip into an instant that many hours back", () => {
    expect(compareSince("8h", NOW)).toBe("2026-11-07T04:00:00.000Z");
    expect(compareSince("7d", NOW)).toBe("2026-10-31T12:00:00.000Z");
  });
});

describe("markerKept", () => {
  it("lets an unfiltered load with every source loaded move the marker", () => {
    expect(markerKept(page(["OK", "OK", "OK", "OK", "OK"]), parseQueueQuery({}))).toBeNull();
  });

  it("lets forbidden and not-collected sources through: they failed nothing", () => {
    expect(markerKept(page(["OK", "FORBIDDEN", "NOT_COLLECTED", "OK", "OK"]), parseQueueQuery({}))).toBeNull();
  });

  it("keeps it while any source failed, and names that source", () => {
    const kept = markerKept(page(["OK", "OK", "FAILED", "OK", "OK"]), parseQueueQuery({}));
    expect(kept?.reason).toBe("failed");
    expect(kept?.failed.map((s) => s.kind)).toEqual(["offense"]);
  });

  it("keeps it under a chosen comparison", () => {
    expect(markerKept(page(["OK"]), parseQueueQuery({ compare: "24h" }))?.reason).toBe("override");
  });

  it.each([{ scope: "mine" }, { kind: "alert" }, { family: "waf" }, { severity: "HIGH" }, { new: "1" }, { offset: "50" }])(
    "keeps it from a narrowed view %j",
    (params) => {
      const q = parseQueueQuery(params);
      expect(isNarrowed(q)).toBe(true);
      expect(markerKept(page(["OK"]), q)?.reason).toBe("narrowed");
    },
  );
});
