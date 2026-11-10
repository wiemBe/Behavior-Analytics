import { describe, expect, it } from "vitest";

import {
  ENTITY_VALUE_MAX,
  entityHref,
  entityKindForDimension,
  entityWindow,
  evidenceEntityHref,
  isSameOriginPath,
  parseEntityQuery,
} from "./entities";

describe("parseEntityQuery", () => {
  it("reads the four parameters and defaults the range to 24 h", () => {
    expect(parseEntityQuery({ value: " 192.0.2.7 " })).toEqual({
      value: "192.0.2.7",
      kind: "",
      range: "24h",
      cursor: "",
      valueTooLong: false,
    });
    expect(parseEntityQuery({ value: "a", kind: "USER", range: "7d", cursor: "c" })).toMatchObject({
      kind: "user",
      range: "7d",
      cursor: "c",
    });
  });

  it("drops an unknown kind or range instead of passing it on", () => {
    expect(parseEntityQuery({ value: "a", kind: "domain", range: "30d" })).toMatchObject({
      kind: "",
      range: "24h",
    });
  });

  it("takes the first of a repeated parameter and flags a value over the API's ceiling", () => {
    expect(parseEntityQuery({ value: ["x", "y"] }).value).toBe("x");
    expect(parseEntityQuery({ value: "a".repeat(ENTITY_VALUE_MAX + 1) }).valueTooLong).toBe(true);
    expect(parseEntityQuery({ value: "a".repeat(ENTITY_VALUE_MAX) }).valueTooLong).toBe(false);
  });
});

describe("entityWindow", () => {
  it("ends now and starts one range earlier", () => {
    const now = Date.parse("2026-11-10T12:00:00Z");
    expect(entityWindow("1h", now)).toEqual({
      start: "2026-11-10T11:00:00.000Z",
      end: "2026-11-10T12:00:00.000Z",
    });
    expect(entityWindow("7d", now).start).toBe("2026-11-03T12:00:00.000Z");
  });
});

describe("entityHref", () => {
  it("encodes every part and never interpolates the value as typed", () => {
    const value = "a/b&c#d eü=1?x";
    const href = entityHref({ value, kind: "user" });
    expect(href).toBe("/entities?kind=user&value=a%2Fb%26c%23d%20e%C3%BC%3D1%3Fx");
    const parsed = new URL(href, "http://x");
    expect(parsed.searchParams.get("value")).toBe(value);
    expect(parsed.searchParams.getAll("value")).toHaveLength(1);
    expect(parsed.hash).toBe("");
  });

  it("omits a missing kind and the default range, and carries a cursor", () => {
    expect(entityHref({ value: "10.0.0.1" })).toBe("/entities?value=10.0.0.1");
    expect(entityHref({ value: "h", kind: "host", range: "7d", cursor: "a.b_c-d" })).toBe(
      "/entities?kind=host&value=h&range=7d&cursor=a.b_c-d",
    );
    expect(entityHref({ value: "h", range: "24h" })).toBe("/entities?value=h");
  });
});

describe("evidence values", () => {
  it("links the address and username dimensions by their name only", () => {
    expect(entityKindForDimension("source_ip")).toBe("ip");
    expect(entityKindForDimension("destination_ip")).toBe("ip");
    expect(entityKindForDimension("username")).toBe("user");
    for (const other of ["destination_port", "event_name", "action", "category", "qid", ""]) {
      expect(entityKindForDimension(other)).toBeNull();
    }
    expect(evidenceEntityHref("destination_port", "10.0.0.1")).toBeNull();
    expect(evidenceEntityHref("source_ip", "10.0.0.1")).toBe("/entities?kind=ip&value=10.0.0.1");
    expect(evidenceEntityHref("username", "DOM\\alice smith")).toBe(
      "/entities?kind=user&value=DOM%5Calice%20smith",
    );
  });

  it("does not link an empty value or one the API would refuse", () => {
    expect(evidenceEntityHref("source_ip", "  ")).toBeNull();
    expect(evidenceEntityHref("username", "u".repeat(ENTITY_VALUE_MAX + 1))).toBeNull();
  });
});

describe("isSameOriginPath", () => {
  it("accepts an application route", () => {
    expect(isSameOriginPath("/offenses/7001?instance_id=1")).toBe(true);
    expect(isSameOriginPath("/anomalies/abc")).toBe(true);
  });

  it.each([
    "https://evil.example/x",
    "//evil.example/x",
    "/\\evil.example",
    "javascript:alert(1)",
    "offenses/1",
    "",
    "/a\nb",
    "/a\\b",
  ])("refuses %j", (href) => {
    expect(isSameOriginPath(href)).toBe(false);
  });
});
