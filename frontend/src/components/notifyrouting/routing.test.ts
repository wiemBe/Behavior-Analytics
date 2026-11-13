import { describe, expect, it } from "vitest";

import { destination, rule } from "./fixtures";
import {
  BLANK_DESTINATION,
  BLANK_RULE,
  FAMILIES,
  destinationBody,
  formFromRule,
  minuteText,
  parseGroupIds,
  parseMinute,
  ruleBody,
  splitList,
  windowWraps,
  type RuleForm,
} from "./routing";

describe("families", () => {
  it("offers identity with the other alert families, after rule", () => {
    expect(FAMILIES).toContain("identity");
    expect(FAMILIES.indexOf("identity")).toBe(FAMILIES.indexOf("rule") + 1);
  });
});

describe("UTC window", () => {
  it("round-trips minutes and HH:MM", () => {
    expect(minuteText(0)).toBe("00:00");
    expect(minuteText(1320)).toBe("22:00");
    expect(minuteText(1439)).toBe("23:59");
    expect(parseMinute("22:00")).toBe(1320);
    expect(parseMinute(" 6:30 ")).toBe(390);
  });

  it("refuses anything that is not a time of day", () => {
    for (const text of ["", "24:00", "12:60", "1200", "12:5", "ab:cd", "-1:00"]) {
      expect(parseMinute(text)).toBeNull();
    }
  });

  it("wraps only when the end is before the start", () => {
    expect(windowWraps(1320, 360)).toBe(true);
    expect(windowWraps(360, 1320)).toBe(false);
  });
});

describe("lists", () => {
  it("trims, drops blanks and duplicates", () => {
    expect(splitList(" a, b,, a ,")).toEqual(["a", "b"]);
    expect(parseGroupIds("12, 14,12")).toEqual([12, 14]);
    expect(parseGroupIds("")).toEqual([]);
    expect(parseGroupIds("12, x")).toBeNull();
    expect(parseGroupIds("1.5")).toBeNull();
  });
});

function form(over: Partial<RuleForm> = {}): RuleForm {
  return { ...BLANK_RULE, name: "R", destinationIds: ["d-1"], ...over };
}

describe("ruleBody", () => {
  it("sends empty filters as null so an edit can clear them", () => {
    const built = ruleBody(form());
    expect(built).toEqual({
      body: expect.objectContaining({
        families: null,
        owners: null,
        source_classes: null,
        group_ids: null,
        window_start_minute: null,
        window_end_minute: null,
        destination_ids: ["d-1"],
        stop_default: false,
      }),
    });
  });

  it("sends the window as minutes after 00:00 UTC", () => {
    expect(ruleBody(form({ windowOn: true, windowStart: "22:00", windowEnd: "06:00" }))).toEqual({
      body: expect.objectContaining({ window_start_minute: 1320, window_end_minute: 360 }),
    });
  });

  it("names the field that cannot be sent", () => {
    expect(ruleBody(form({ windowOn: true, windowStart: "22:00", windowEnd: "22:00" }))).toEqual({
      problem: "window",
    });
    expect(ruleBody(form({ windowOn: true, windowStart: "", windowEnd: "06:00" }))).toEqual({
      problem: "window",
    });
    expect(ruleBody(form({ groups: "a" }))).toEqual({ problem: "groups" });
    expect(ruleBody(form({ priority: "-1" }))).toEqual({ problem: "priority" });
    expect(ruleBody(form({ priority: "100001" }))).toEqual({ problem: "priority" });
  });

  it("fills an edit form from a stored rule", () => {
    const edit = formFromRule(
      rule({ window_start_minute: 1320, window_end_minute: 360, group_ids: [12], owners: ["a"] }),
    );
    expect(edit).toMatchObject({
      windowOn: true,
      windowStart: "22:00",
      windowEnd: "06:00",
      groups: "12",
      owners: "a",
      destinationIds: ["d-1"],
    });
  });
});

describe("destinationBody", () => {
  it("a new destination sends its URL, secret and headers", () => {
    const body = destinationBody({
      ...BLANK_DESTINATION,
      name: " SOAR ",
      url: " https://soar.example/hook?k=1 ",
      secret: "s3",
      headers: [{ name: "X-Team", value: "blue" }, { name: " ", value: "ignored" }],
    });
    expect(body).toEqual({
      name: "SOAR",
      enabled: true,
      allow_http: false,
      allow_private: false,
      url: "https://soar.example/hook?k=1",
      hmac_secret: "s3",
      headers: { "X-Team": "blue" },
    });
  });

  it("an edit that touches neither URL, secret nor headers sends none of them", () => {
    const item = destination();
    const body = destinationBody({
      ...BLANK_DESTINATION,
      id: item.id,
      name: item.name,
      secretMode: "keep",
      secret: "typed-then-kept",
      replaceHeaders: false,
      headers: [{ name: "X-Typed", value: "v" }],
    });
    expect(body).not.toHaveProperty("url");
    expect(body).not.toHaveProperty("hmac_secret");
    expect(body).not.toHaveProperty("headers");
  });

  it("an edit clears the secret with null and replaces all headers when asked", () => {
    expect(
      destinationBody({ ...BLANK_DESTINATION, id: "d-1", name: "x", secretMode: "clear" }),
    ).toHaveProperty("hmac_secret", null);
    expect(
      destinationBody({
        ...BLANK_DESTINATION,
        id: "d-1",
        name: "x",
        replaceHeaders: true,
        headers: [],
      }),
    ).toHaveProperty("headers", {});
  });
});
