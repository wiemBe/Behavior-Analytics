import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import tr from "../../messages/tr";
import { renderBackendCode, renderBackendMessage } from "./backendMessage";
import { createTextTranslator } from "./textTranslator";

const TR = createTextTranslator("tr", tr);
const EN = createTextTranslator("en", en);

const SCOPE = { code: "waf.scope.lower_bound_increase", params: {} };
const Z = { code: "waf.basis.z", params: { score: 6.2, threshold: 3.5 } };

describe("MSG-6b a nested sentence continues the sentence that holds it", () => {
  const rate = (scope: unknown) => ({
    code: "waf.rate.fired_lower",
    params: {
      observed: 412,
      scope,
      expected: 41.5,
      ratio_text: { code: "waf.rate.ratio_none", params: {} },
      delta: 370,
      basis: Z,
    },
  });

  it("lowers the first letter of a WAF scope inside the clause that names it", () => {
    expect(renderBackendCode(rate(SCOPE), "tr", TR)).toContain(
      "(alt sınır: sayım bir alt sınırdır: artış kanıtlanabilir, normal davranış kanıtlanamaz)",
    );
  });

  it("lowers an IPS scope the same way, and only the nested sentence after its role", () => {
    const ips = {
      code: "ips.spike.fired_lower",
      params: {
        observed: 1520,
        unit: { code: "ips.noun.events", params: {} },
        scope: {
          code: "ips.scope.role",
          params: { role: { code: "ips.role.volume", params: {} }, reason: SCOPE },
        },
        subject: {
          code: "ips.subject.named",
          params: { kind: { code: "ips.label.destination_port", params: {} }, name: "443/tcp" },
        },
        expected: 100,
        ratio_text: { code: "waf.rate.ratio_multiple", params: { ratio: 15.2 } },
        delta: 1420,
        basis: Z,
      },
    };
    expect(renderBackendCode(ips, "tr", TR)).toBe(
      "En az 1.520 olay (alt sınır: hacim: sayım bir alt sınırdır: artış kanıtlanabilir, normal davranış kanıtlanamaz), hedef port 443/tcp için baseline medyanı olan 100,0 değerinin üzerinde (beklenenin 15,20x katı, +1.420 olay/zaman dilimi); robust z=6,20 (eşik +3,5)",
    );
  });

  it("keeps the capital of a nested sentence that opens the sentence", () => {
    const wrap = {
      code: "ips.guard.not_proven_lower_bound",
      params: {
        reason: { code: "waf.basis.z_not_reached", params: { score: 2.1, threshold: 3.5 } },
        scope: {
          code: "ips.scope.role",
          params: { role: { code: "ips.role.volume", params: {} }, reason: SCOPE },
        },
      },
    };
    expect(renderBackendCode(wrap, "tr", TR)).toBe(
      "Robust z=2,10, +3,5 değerine ulaşmadı; sayım bir alt sınırdır (hacim: sayım bir alt sınırdır: artış kanıtlanabilir, normal davranış kanıtlanamaz), bu nedenle ondan normal bir karar çıkarılamaz",
    );
    const wafWrap = {
      code: "waf.guard.not_proven_lower_bound",
      params: { reason: wrap.params.reason, scope: SCOPE },
    };
    expect(renderBackendCode(wafWrap, "tr", TR)).toBe(
      "Robust z=2,10, +3,5 değerine ulaşmadı; oranın payı bir alt sınırdır (sayım bir alt sınırdır: artış kanıtlanabilir, normal davranış kanıtlanamaz), bu nedenle ondan normal bir karar çıkarılamaz",
    );
  });

  it("keeps an acronym or a name that opens a nested sentence", () => {
    const ratio = {
      code: "waf.ratio.denominator_below_min",
      params: { count: 30, noun: { code: "waf.noun.waf_action_requests", params: {} }, minimum: 50 },
    };
    expect(renderBackendCode(ratio, "tr", TR)).toContain("30 WAF eylemi taşıyan istekler");
    const http = {
      code: "waf.ratio.denominator_below_min",
      params: { count: 30, noun: { code: "waf.noun.http_4xx", params: {} }, minimum: 50 },
    };
    expect(renderBackendCode(http, "tr", TR)).toContain("30 HTTP 4xx yanıtları");
  });

  it("leaves English untouched: the API text, and the English catalog's own render", () => {
    const params = rate(SCOPE).params;
    expect(
      renderBackendMessage({ text: "the API text", code: "waf.rate.fired_lower", params }, "en", EN),
    ).toBe("the API text");
    expect(renderBackendCode(rate(SCOPE), "en", EN)).toContain(
      "(a lower bound: the count is a lower bound: an increase can be proven, normal behaviour cannot)",
    );
  });
});
