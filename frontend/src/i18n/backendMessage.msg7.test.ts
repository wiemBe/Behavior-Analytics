import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import tr from "../../messages/tr";
import { renderBackendCode, renderBackendMessage } from "./backendMessage";
import { createTextTranslator } from "./textTranslator";

const TR = createTextTranslator("tr", tr);
const EN = createTextTranslator("en", en);

describe("MSG-7 setting names render verbatim", () => {
  it.each([
    ["waf_collection_enabled", "waf_collection_enabled kapalı"],
    ["ips_collection_enabled", "ips_collection_enabled kapalı"],
    ["pw_collection_enabled", "pw_collection_enabled kapalı"],
    ["WAF_COLLECTION_ENABLED", "WAF_COLLECTION_ENABLED kapalı"],
  ])("the family reason keeps %s in Turkish", (setting, expected) => {
    expect(
      renderBackendCode({ code: "custom_rule.family.collection_off", params: { setting } }, "tr", TR),
    ).toBe(expected);
  });

  it("keeps the English text of the family reason as the API sent it", () => {
    expect(
      renderBackendMessage(
        {
          text: "waf_collection_enabled is off",
          code: "custom_rule.family.collection_off",
          params: { setting: "waf_collection_enabled" },
        },
        "en",
        EN,
      ),
    ).toBe("waf_collection_enabled is off");
  });

  it("keeps the platform switch name upper case in Turkish", () => {
    expect(
      renderBackendCode({ code: "custom_rule.status.platform_off", params: {} }, "tr", TR),
    ).toBe("CUSTOM_RULES_ENABLED kapalı");
  });
});

describe("MSG-7 timestamp styles", () => {
  const stale = (code: string, bucket: string) =>
    renderBackendCode({ code, params: { bucket, age: { kind: "duration", seconds: 60 } } }, "tr", TR);

  it("the minute style shows date, hour and minute only", () => {
    expect(stale("custom_rule.status.stale.minute", "2026-10-14T08:00:00+00:00")).toContain(
      "2026-10-14 08:00 UTC tarihinden",
    );
  });

  it("the default style keeps its seconds", () => {
    expect(stale("custom_rule.status.stale.second", "2026-10-14T08:00:07+00:00")).toContain(
      "2026-10-14 08:00:07 UTC tarihinden",
    );
  });
});

describe("MSG-7 operators in Turkish", () => {
  const cases: [string, string][] = [
    ["GT", ">"],
    ["GE", "≥"],
    ["LT", "<"],
    ["LE", "≤"],
    ["EQ", "="],
  ];

  it.each(cases)("a custom-rule alert renders %s as %s with the threshold label", (operator, sign) => {
    expect(
      renderBackendCode(
        {
          code: "alert.custom_rule.description.exact_unit",
          params: { name: "Kural", observed: 5, unit: "olay", operator, threshold: 1000 },
        },
        "tr",
        TR,
      ),
    ).toBe(`Kural, 5\u00a0olay değerini gözlemledi (eşik ${sign} 1.000\u00a0olay)`);
  });

  it.each(cases)("a search alert renders %s as %s with the threshold label", (operator, sign) => {
    expect(
      renderBackendCode(
        {
          code: "alert.search.threshold.lower_bound",
          params: { name: "Arama", observed: 1204, operator, threshold: 1000 },
        },
        "tr",
        TR,
      ),
    ).toBe(`Arama, en az 1.204 sonucunu döndürdü (eşik ${sign} 1.000)`);
  });

  it("every operator template of the family carries the label", () => {
    for (const code of [
      "alert.custom_rule.description.exact",
      "alert.custom_rule.description.lower_bound",
      "alert.custom_rule.description.unmeasured",
    ]) {
      const params: Record<string, unknown> = { name: "K", operator: "GT", threshold: 1 };
      if (!code.endsWith("unmeasured")) params.observed = 2;
      expect(renderBackendCode({ code, params }, "tr", TR)).toContain("(eşik > 1)");
    }
  });
});
