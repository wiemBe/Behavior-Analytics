// MSG-1b: the authoring reasons (custom-rule draft, AQL, threshold) and the remaining conflicts
// reach the reader in its language. English keeps the server's text; Turkish words a known code;
// an uncoded reason (an old server's, or a free sentence) shows the text as sent. A count is
// grouped (`1.204`) and a quoted user value keeps its quotes.

import { describe, expect, it } from "vitest";

import { classifySaveError as classifyRuleError, reasonTexts } from "@/components/customrules/authoring";
import { capMessage, classifySaveError as classifySearchError } from "@/components/searches/authoring";
import { ApiError } from "@/lib/api";
import { conflictInfo, conflictMessage, fieldForMessage, serverMessage } from "@/lib/fieldMapping";
import { ruleFailure } from "@/lib/fleet";
import { fleetTextOf } from "@/lib/fleetText";
import { reasonSentences } from "@/lib/refusal";

import codesJson from "./backendCodes.generated.json";
import { renderBackendMessage, type BackendCodeArtifact } from "./backendMessage";
import { renderRefusal } from "./refusalMessage";
import { createLocaleTranslator } from "./testTranslator";

const tr = createLocaleTranslator("tr");
const en = createLocaleTranslator("en");
const CODES = codesJson as BackendCodeArtifact;

const body = (status: number, detail: unknown, extra: Record<string, unknown> = {}) =>
  new ApiError(status, typeof detail === "string" ? detail : "failed", detail, { detail, ...extra });

const reasons = (
  texts: string[],
  codes: Array<{ code: string; params: Record<string, unknown> } | null>,
) => body(422, { message: texts.join("; "), reasons: texts, reason_codes: codes });

describe("the reasons of an authoring body", () => {
  it("pairs each reason with its code, and leaves a null or missing entry uncoded", () => {
    const error = reasons(["a", "b", "c"], [{ code: "x.y", params: { n: 1 } }, null]);
    expect(reasonSentences(error)).toEqual([
      { text: "a", code: "x.y", params: { n: 1 } },
      { text: "b", code: null, params: null },
      { text: "c", code: null, params: null },
    ]);
  });

  it("is null for a detail that is not a reasons body", () => {
    expect(reasonSentences(body(422, "a string"))).toBeNull();
    expect(reasonSentences(body(422, [{ msg: "x" }]))).toBeNull();
    expect(reasonSentences(body(422, { message: "m" }))).toBeNull();
    expect(reasonSentences({ detail: undefined })).toBeNull();
  });

  it("skips a reason that is not text and ignores a malformed code entry", () => {
    const error = body(422, { reasons: ["a", 7, "b"], reason_codes: [{ code: 5 }, null, "x"] });
    expect(reasonSentences(error)).toEqual([
      { text: "a", code: null, params: null },
      { text: "b", code: null, params: null },
    ]);
  });
});

describe("custom-rule draft reasons", () => {
  const error = reasons(
    [
      "metric \"it's\" is not in the custom-rule catalog",
      "sustain_buckets must be at least 1",
      "a reason with no code",
    ],
    [
      { code: "ruledraft.metric_unknown", params: { metric: "\"it's\"" } },
      { code: "ruledraft.sustain_min", params: {} },
      null,
    ],
  );

  it("keeps the server's English in English", () => {
    expect(classifyRuleError(error, en, "en")).toEqual({
      kind: "reasons",
      reasons: [
        "metric \"it's\" is not in the custom-rule catalog",
        "sustain_buckets must be at least 1",
        "a reason with no code",
      ],
    });
  });

  it("words each coded reason in Turkish, quotes intact, and leaves the uncoded one", () => {
    expect(classifyRuleError(error, tr, "tr")).toEqual({
      kind: "reasons",
      reasons: [
        "\"it's\" metriği özel kural kataloğunda yok",
        "sustain_buckets en az 1 olmalıdır",
        "a reason with no code",
      ],
    });
    expect(reasonTexts(error, tr, "tr")).toHaveLength(3);
  });

  it("words the enabled-rule cap 409 in Turkish with grouped counts", () => {
    const cap = body(409, "at most 1204 rules may be enabled at once (1204 already are)", {
      detail_code: "ruledraft.enabled_cap",
      detail_params: { max_enabled: 1204, enabled_count: 1204 },
    });
    expect(classifyRuleError(cap, en, "en")).toEqual({
      kind: "cap",
      detail: "at most 1204 rules may be enabled at once (1204 already are)",
    });
    expect(classifyRuleError(cap, tr, "tr")).toEqual({
      kind: "cap",
      detail: "Aynı anda en fazla 1.204 kural etkin olabilir (şu an 1.204 etkin)",
    });
  });
});

describe("search authoring reasons", () => {
  const i18nTr = { t: tr, locale: "tr" as const };
  const i18nEn = { t: en, locale: "en" as const };
  const error = reasons(
    [
      "disallowed keyword(s): DROP, INSERT",
      "LIMIT 20000 exceeds the maximum 10000 rows",
      "time range 48.0h exceeds the maximum 24h",
    ],
    [
      { code: "search.aql.disallowed_keywords", params: { keywords: "DROP, INSERT" } },
      { code: "search.aql.limit_exceeded", params: { limit: 20000, max_rows: 10000 } },
      { code: "search.aql.time_range_exceeded", params: { window_hours: "48.0", max_hours: 24 } },
    ],
  );

  it("shows the server's English in English", () => {
    expect(classifySearchError(error, i18nEn)).toEqual({
      kind: "reasons",
      reasons: [
        "disallowed keyword(s): DROP, INSERT",
        "LIMIT 20000 exceeds the maximum 10000 rows",
        "time range 48.0h exceeds the maximum 24h",
      ],
    });
  });

  it("words each reason in Turkish, grouping the counts and keeping the unit apart", () => {
    expect(classifySearchError(error, i18nTr)).toEqual({
      kind: "reasons",
      reasons: [
        "İzin verilmeyen anahtar sözcükler: DROP, INSERT",
        "LIMIT 20.000, en fazla 10.000 satırlık üst sınırı aşıyor",
        "48.0 sa uzunluğundaki zaman aralığı en fazla 24 sa olabilir",
      ],
    });
  });

  it("words the unreachable-threshold reason and an old server's reasons alike", () => {
    const threshold = reasons(
      ["threshold GT 10001 can never be reached: x"],
      [
        {
          code: "search.threshold.unreachable",
          params: { operator: "GT", threshold: "10001", max_result_rows: 10000 },
        },
      ],
    );
    expect(classifySearchError(threshold, i18nTr)).toMatchObject({
      kind: "reasons",
      reasons: [expect.stringContaining("GT 10001 eşiğine hiçbir zaman ulaşılamaz")],
    });
    expect(classifySearchError(threshold, i18nTr)).toMatchObject({
      reasons: [expect.stringContaining("max_result_rows (10.000) satır")],
    });
    const old = body(422, { message: "m", reasons: ["old reason"] });
    expect(classifySearchError(old, i18nTr)).toEqual({ kind: "reasons", reasons: ["old reason"] });
  });

  it("words the enabled-search cap, and capMessage wraps the Turkish clause", () => {
    const cap = body(409, "the limit is 1204 enabled searches", {
      detail_code: "search.enabled_cap",
      detail_params: { limit: 1204 },
    });
    expect(classifySearchError(cap, i18nEn)).toEqual({
      kind: "cap",
      detail: "the limit is 1204 enabled searches",
    });
    const refusal = classifySearchError(cap, i18nTr);
    expect(refusal).toEqual({ kind: "cap", detail: "Etkin arama sınırı 1.204" });
    if (refusal.kind !== "cap") throw new Error("cap");
    expect(capMessage(refusal.detail, i18nTr)).toBe(
      "Etkinleştirilmedi: Etkin arama sınırı 1.204. Başka bir aramayı devre dışı bırakın, sonra bunu etkinleştirin.",
    );
  });
});

describe("classification rule conflicts", () => {
  const duplicate = body(409, "a rule with this instance, kind and pattern already exists", {
    detail_code: "classification.rule.duplicate",
    detail_params: {},
  });

  it("keeps English as sent and words the code in Turkish", () => {
    expect(ruleFailure(duplicate, fleetTextOf(en, "en"))).toEqual({
      kind: "error",
      text: "a rule with this instance, kind and pattern already exists",
      field: "pattern",
    });
    expect(ruleFailure(duplicate, fleetTextOf(tr, "tr"))).toEqual({
      kind: "error",
      text: "Bu instance, tür ve desene sahip bir kural zaten var",
      field: "pattern",
    });
  });

  it("words the built-in refusals and leaves an uncoded 422 as sent", () => {
    const edit = body(409, "a built-in rule can only be enabled or disabled", {
      detail_code: "classification.rule.built_in_edit",
      detail_params: {},
    });
    expect(ruleFailure(edit, fleetTextOf(tr, "tr")).text).toBe(
      "Yerleşik bir kural yalnızca açılıp kapatılabilir",
    );
    const regex = body(422, "pattern is not a valid regular expression");
    expect(ruleFailure(regex, fleetTextOf(tr, "tr"))).toEqual({
      kind: "error",
      text: "pattern is not a valid regular expression",
      field: "pattern",
    });
  });
});

describe("profile conflicts and input refusals", () => {
  const conflict = body(409, {
    message: "profile version conflict; current version is 12",
    code: "validation.profile.conflict.version",
    params: { current: 12 },
    current_version: 12,
    updated_by: "k",
    pending_since: null,
  });
  const render = (locale: "en" | "tr") => (sentence: Parameters<typeof renderBackendMessage>[0]) =>
    renderBackendMessage(sentence, locale, locale === "tr" ? tr : en);

  it("carries the code from the conflict body", () => {
    expect(conflictInfo(conflict)).toMatchObject({
      message: "profile version conflict; current version is 12",
      code: "validation.profile.conflict.version",
      params: { current: 12 },
      version: 12,
    });
    expect(conflictInfo(body(409, { message: null, current_version: 1 }))).not.toHaveProperty("code");
  });

  it("words the conflict in Turkish and keeps English as sent", () => {
    const info = conflictInfo(conflict);
    expect(conflictMessage(info, { t: en, locale: "en" }, render("en"))).toMatch(
      /^profile version conflict; current version is 12 /,
    );
    expect(conflictMessage(info, { t: tr, locale: "tr" }, render("tr"))).toMatch(
      /^Profil sürümü çakışması; geçerli sürüm 12 /,
    );
    expect(conflictMessage(info, { t: en, locale: "en" })).toMatch(
      /^profile version conflict; current version is 12 /,
    );
  });

  it("words the enable refusal with the status label", () => {
    const refused = body(409, {
      message: "profile version 4 has not validated (status INVALID); run validation first",
      code: "validation.profile.conflict.not_validated",
      params: { profile_version: 4, validation_status: "INVALID" },
    });
    expect(serverMessage(refused)).toBe(
      "profile version 4 has not validated (status INVALID); run validation first",
    );
    expect(serverMessage(refused, render("tr"))).toMatch(
      /^Profil sürümü 4 doğrulanmadı \(durum: .+\); önce doğrulamayı çalıştırın$/,
    );
    expect(serverMessage(refused, render("tr"))).not.toContain("INVALID");
  });

  it("finds the field in the server's English while the shown text is Turkish", () => {
    const refused = body(422, {
      message: "invalid QRadar property name 'bad;name': letters, digits, underscore, space and hyphen only, at most 64 characters",
      code: "validation.waf.input.property_name",
      params: { name: "'bad;name'" },
    });
    const shown = serverMessage(refused, render("tr"));
    expect(shown).toContain("'bad;name' geçerli bir QRadar özellik adı değil");
    const schema = { fields: [{ key: "URL" }, { key: "METHOD" }] } as never;
    const draft = { mapping: { URL: "bad;name", METHOD: "zzz" } } as never;
    expect(fieldForMessage(schema, draft, serverMessage(refused))).toBe("URL");
  });

  it("falls back to the server's text for an uncoded or unknown code", () => {
    const plain = body(422, { message: "An action map needs an action property" });
    expect(serverMessage(plain, render("tr"))).toBe("An action map needs an action property");
    const unknown = body(422, { message: "words", code: "validation.nope.nothing", params: {} });
    expect(serverMessage(unknown, render("tr"))).toBe("words");
  });
});

describe("agreement of the lean and the full reader", () => {
  const families = [
    "ruledraft.",
    "search.aql.",
    "search.threshold.",
    "search.enabled_cap",
    "classification.rule.",
    "validation.profile.conflict.",
    "validation.waf.input.",
    "validation.ips.input.",
  ];
  const codes = Object.entries(CODES).filter(([code]) => families.some((f) => code.startsWith(f)));

  it("covers every MSG-1b code", () => {
    expect(codes.length).toBe(46);
  });

  it.each(codes)("%s reads the same in Turkish, or needs the full reader for an enum", (code, spec) => {
    const sentence = { text: spec.sample_english, code, params: spec.sample };
    const full = renderBackendMessage(sentence, "tr", tr);
    expect(full).not.toBe(spec.sample_english);
    const lean = renderRefusal(sentence, "tr", tr);
    if (Object.values(spec.params).some((param) => param.kind === "enum")) {
      // The lean reader prints the raw enum value; the editors use the full reader for it.
      expect(code).toBe("validation.profile.conflict.not_validated");
    } else {
      expect(lean).toBe(full);
    }
    expect(renderRefusal(sentence, "en", en)).toBe(spec.sample_english);
  });
});

describe("a count in the lean reader", () => {
  it("is grouped in Turkish and left to the server's text in English", () => {
    const sentence = {
      text: "LIMIT 1204 exceeds the maximum 1204 rows",
      code: "search.aql.limit_exceeded",
      params: { limit: 1204, max_rows: 1204 },
    };
    expect(renderRefusal(sentence, "tr", tr)).toBe(
      "LIMIT 1.204, en fazla 1.204 satırlık üst sınırı aşıyor",
    );
    expect(renderRefusal(sentence, "en", en)).toBe("LIMIT 1204 exceeds the maximum 1204 rows");
  });
});
