import { describe, expect, it } from "vitest";

import { ACTIVITY_PAGE, COMMENT_MAX, NOTE_MAX, OWNER_MAX, eventSentence, parseTriageQuery, triageFamilyOf } from "./triage";
import type { TriageEvent } from "./api";

describe("triageFamilyOf", () => {
  it("maps every subject kind that has a triage family", () => {
    expect(triageFamilyOf({ subject_kind: "LOG_SOURCE" })).toBe("log_source");
    expect(triageFamilyOf({ subject_kind: "WAF_ENDPOINT" })).toBe("waf");
    expect(triageFamilyOf({ subject_kind: "IPS_ENTITY" })).toBe("ips");
    expect(triageFamilyOf({ subject_kind: "IPS_SOURCE" })).toBe("ips");
    expect(triageFamilyOf({ subject_kind: "PW_VALUE" })).toBe("property_watch");
  });
  it("treats a missing kind as a Phase A log source", () => {
    expect(triageFamilyOf({})).toBe("log_source");
  });
  it("returns null for a kind it does not know, never a guess", () => {
    expect(triageFamilyOf({ subject_kind: "CUSTOM_RULE" as never })).toBeNull();
    expect(triageFamilyOf({ subject_kind: "constructor" as never })).toBeNull();
  });
});

describe("the bounds mirror the backend's", () => {
  it("are 255, 1000, 2000 and a page of 50", () => {
    expect([OWNER_MAX, NOTE_MAX, COMMENT_MAX, ACTIVITY_PAGE]).toEqual([255, 1000, 2000, 50]);
  });
});

describe("parseTriageQuery", () => {
  it("reads the three filters", () => {
    expect(parseTriageQuery({ triage_status: "CLOSED", owner: "me", disposition: "NOISE" })).toEqual({
      triage_status: "CLOSED",
      owner: "me",
      disposition: "NOISE",
    });
  });
  it("is empty by default", () => {
    expect(parseTriageQuery({})).toEqual({ triage_status: "", owner: "", disposition: "" });
  });
  it("lets the form's owner kind win over a stale owner", () => {
    expect(parseTriageQuery({ owner_kind: "none", owner: "x" }).owner).toBe("none");
    expect(parseTriageQuery({ owner_kind: "me" }).owner).toBe("me");
    expect(parseTriageQuery({ owner_kind: "named", owner_name: " k.osei " }).owner).toBe("k.osei");
    expect(parseTriageQuery({ owner_kind: "", owner: "k.osei" }).owner).toBe("k.osei");
  });
  it("drops what the backend would refuse", () => {
    expect(parseTriageQuery({ triage_status: "new", disposition: "x" })).toMatchObject({
      triage_status: "",
      disposition: "",
    });
    expect(parseTriageQuery({ owner: "a".repeat(256) }).owner).toBe("");
    expect(parseTriageQuery({ owner: "a\nb" }).owner).toBe("");
    expect(parseTriageQuery({ owner: "a".repeat(255) }).owner).toHaveLength(255);
  });
  it("takes the first of a repeated parameter", () => {
    expect(parseTriageQuery({ triage_status: ["NEW", "CLOSED"] }).triage_status).toBe("NEW");
  });
});

describe("eventSentence", () => {
  const words = {
    status: (v: string) => `S:${v}`,
    disposition: (v: string) => `D:${v}`,
    unassigned: "none",
  };
  const say = (key: string, values: Record<string, string> = {}) =>
    `${key}|${Object.entries(values).map(([k, v]) => `${k}=${v}`).join(",")}`;
  const ev = (over: Partial<TriageEvent>): TriageEvent => ({
    id: "1", kind: "STATUS", actor: "x", created_at: "", from_value: null, to_value: null,
    comment: null, triage_version: 1, ...over,
  });

  it("words a status change, defaulting a missing origin to New", () => {
    expect(eventSentence(ev({ from_value: null, to_value: "CLOSED" }), words, say)).toBe(
      "events.status|from=S:NEW,to=S:CLOSED",
    );
  });
  it("words an owner set, change and removal", () => {
    expect(eventSentence(ev({ kind: "OWNER", to_value: "a" }), words, say)).toBe("events.ownerSet|from=,to=a");
    expect(eventSentence(ev({ kind: "OWNER", from_value: "a", to_value: "b" }), words, say)).toBe(
      "events.ownerChanged|from=a,to=b",
    );
    expect(eventSentence(ev({ kind: "OWNER", from_value: "a" }), words, say)).toBe("events.ownerCleared|from=a");
    expect(eventSentence(ev({ kind: "OWNER" }), words, say)).toBe("events.ownerCleared|from=none");
  });
  it("words a disposition set and cleared, and a note", () => {
    expect(eventSentence(ev({ kind: "DISPOSITION", to_value: "NOISE" }), words, say)).toBe("events.dispositionSet|to=D:NOISE");
    expect(eventSentence(ev({ kind: "DISPOSITION" }), words, say)).toBe("events.dispositionCleared|");
    expect(eventSentence(ev({ kind: "NOTE", comment: "t" }), words, say)).toBe("events.noteSet|");
    expect(eventSentence(ev({ kind: "NOTE" }), words, say)).toBe("events.noteCleared|");
  });
});
