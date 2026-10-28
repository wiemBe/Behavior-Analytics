import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import tr from "../../messages/tr";
import {
  type BackendCodeArtifact,
  type BackendCodeSpec,
  renderBackendCode,
  renderBackendMessage,
} from "./backendMessage";
import { createTextTranslator, type TextTranslator } from "./textTranslator";

const EN = createTextTranslator("en", en);
const TR = createTextTranslator("tr", tr);
const PILOT = "explanation.dimension.unpopulated";
const API_TEXT = "field is not populated for this log source";

describe("backend message reader", () => {
  it("renders the pilot sentence in Turkish", () => {
    expect(
      renderBackendMessage({ text: API_TEXT, code: PILOT, params: {} }, "tr", TR),
    ).toBe("Bu alan, bu log source'un olaylarında dolu değil");
  });

  it.each([
    ["absent", {}],
    ["null", { code: null, params: null }],
    ["unknown", { code: "unknown.message", params: {} }],
    ["inherited", { code: "constructor", params: {} }],
  ])("falls back to API English for a %s code", (_name, coded) => {
    expect(renderBackendMessage({ text: API_TEXT, ...coded }, "tr", TR)).toBe(API_TEXT);
  });

  it("falls back when the Turkish catalog has no own message", () => {
    const missing = ((key: string) => key) as TextTranslator;
    missing.has = () => false;
    expect(
      renderBackendMessage({ text: API_TEXT, code: PILOT, params: {} }, "tr", missing),
    ).toBe(API_TEXT);
  });

  it("falls back when the translator reports a key but cannot render it", () => {
    const malformed = ((key: string) => key) as TextTranslator;
    malformed.has = () => true;
    expect(
      renderBackendMessage({ text: API_TEXT, code: PILOT, params: {} }, "tr", malformed),
    ).toBe(API_TEXT);
  });

  it("renders API English verbatim even when a code is present", () => {
    const serverSpelling = "API text stays 31.8% exactly";
    expect(
      renderBackendMessage({ text: serverSpelling, code: PILOT, params: {} }, "en", EN),
    ).toBe(serverSpelling);
  });
});

const ALL_KINDS: BackendCodeArtifact = {
  "test.child": {
    template: "child",
    params: {},
    sample: {},
    sample_english: "child",
    sample_english_params: {},
  },
  "test.all": {
    template: "",
    params: {
      count: { kind: "count" },
      number: { kind: "number" },
      percent: { kind: "percent", precision: 1 },
      points: { kind: "points", precision: 1 },
      duration: { kind: "duration" },
      timestamp: { kind: "timestamp" },
      enum: { kind: "enum", enum_group: "anomalyState" },
      list: { kind: "list" },
      ref: { kind: "ref", ref_codes: ["test.child"] },
      text: { kind: "text" },
    },
    sample: {},
    sample_english: "",
    sample_english_params: {},
  },
};

const TEST_MESSAGES = {
  backend: {
    test: {
      child: "child",
      all: "{count}|{number}|{percent}|{points}|{duration}|{timestamp}|{enum}|{list}|{ref}|{text}",
    },
  },
};

const PARAMS = {
  count: 1204,
  number: 12.5,
  percent: { kind: "percent", ratio: 0.318, precision: 1 },
  points: { kind: "points", points: 29.7, precision: 1, signed: true },
  duration: { kind: "duration", seconds: 5400 },
  timestamp: "2026-10-28T09:15:00Z",
  enum: "OPEN",
  list: { kind: "list", items: ["alpha", "beta"] },
  ref: { code: "test.child", params: {} },
  text: "verbatim",
};

describe("backend message parameter formatting", () => {
  it("formats every kind in English", () => {
    const say = createTextTranslator("en", { ...en, ...TEST_MESSAGES });
    expect(renderBackendCode({ code: "test.all", params: PARAMS }, "en", say, ALL_KINDS)).toBe(
      "1,204|12.5|31.8 %|+29.7 pp|1 h 30 min|2026-10-28 09:15:00 UTC|OPEN|alpha and beta|child|verbatim",
    );
  });

  it("formats every kind in Turkish", () => {
    const say = createTextTranslator("tr", { ...tr, ...TEST_MESSAGES });
    expect(renderBackendCode({ code: "test.all", params: PARAMS }, "tr", say, ALL_KINDS)).toBe(
      "1.204|12,5|%31,8|+29,7 puan|1 sa 30 dk|2026-10-28 09:15:00 UTC|AÇIK|alpha ve beta|child|verbatim",
    );
  });

  it("renders an unknown enum value raw", () => {
    const say = createTextTranslator("tr", { ...tr, ...TEST_MESSAGES });
    expect(
      renderBackendCode(
        { code: "test.all", params: { ...PARAMS, enum: "FUTURE" } },
        "tr",
        say,
        ALL_KINDS,
      ),
    ).toContain("|FUTURE|");
  });

  it("bounds recursive refs and lets the caller fall back", () => {
    const spec = (template: string, params: BackendCodeSpec["params"]) => ({
      template,
      params,
      sample: {},
      sample_english: "",
      sample_english_params: {},
    });
    // Every level is well formed and ends in a known leaf: only the depth bound can refuse it.
    const nested: BackendCodeArtifact = {
      "test.loop": spec("{ref}", { ref: { kind: "ref", ref_codes: ["test.loop", "test.leaf"] } }),
      "test.leaf": spec("leaf", {}),
    };
    const say = createTextTranslator("tr", {
      backend: { test: { loop: "{ref}", leaf: "leaf" } },
      units: tr.units,
      waf: tr.waf,
      enums: tr.enums,
    });
    const chain = (levels: number) => {
      let value: Record<string, unknown> = { code: "test.leaf", params: {} };
      for (let depth = 0; depth < levels; depth += 1) {
        value = { code: "test.loop", params: { ref: value } };
      }
      return value.params as Record<string, unknown>;
    };
    const render = (levels: number) =>
      renderBackendMessage(
        { text: "safe fallback", code: "test.loop", params: chain(levels) },
        "tr",
        say,
        nested,
      );
    expect(render(3)).toBe("leaf");
    expect(render(12)).toBe("safe fallback");
  });
});
