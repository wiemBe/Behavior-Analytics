import { parse, TYPE, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import tr from "../../messages/tr";
import frozen from "./tr-frozen-keys.json";
import { flattenCatalog as flatten, FROZEN_TR_KEYS, parityProblems } from "./trFrozen";

function argumentsOf(message: string): string[] {
  const names = new Set<string>();
  const visit = (elements: MessageFormatElement[]) => {
    for (const element of elements) {
      if (
        element.type === TYPE.argument ||
        element.type === TYPE.number ||
        element.type === TYPE.date ||
        element.type === TYPE.time ||
        element.type === TYPE.select ||
        element.type === TYPE.plural
      ) {
        names.add(element.value);
      }
      if (element.type === TYPE.select || element.type === TYPE.plural) {
        Object.values(element.options).forEach((option) => visit(option.value));
      } else if (element.type === TYPE.tag) {
        visit(element.children);
      }
    }
  };
  visit(parse(message));
  return [...names].sort();
}

// Identical values are limited to product/technical terms, punctuation-only text, and the two
// language names, which the selector deliberately presents in each language's own spelling.
const IDENTICAL_ALLOWLIST = new Set([
  // I18N-12 (D-39): "coverage" and "scope" stay English in Turkish, like log source.
  "catalog.detection.link",
  "history.table.scope",
  "propertyWatch.common.scope",
  "propertyWatch.editor.scopeLegend",
  "propertyWatch.investigation.scope",
  "propertyWatch.list.scope",
  "propertyWatch.preview.coverage",
  "shell.nav.coverage",
  "waf.chart.axisCoverage",
  "waf.chart.coverageLine",
  "waf.chart.keyCoverage",
  "waf.metrics.coverage",
  "waf.sources.coverage",
  "waf.sourcesWidget.coverage",
  "widgets.propertyWatch.coverageBadge",
  // MSG-3: a dependency's QRadar name is passed through verbatim in both locales.
  "backend.rule_health.label.named",
  // MSG-6: the method and the normalized endpoint, both verbatim in both locales.
  "backend.waf.subject.named",
  // MSG-6b: the role and the reason of a scope, an entity's kind and name, `coverage` (a kept
  // English term, D-39) and `fan-out` (the technical term, as in the Turkish summary).
  "backend.ips.label.port_fanout",
  "backend.ips.role.coverage",
  "backend.ips.scope.role",
  "backend.ips.subject.named",
  // MSG-8b: the defensive re-validation error is the joined, already-worded reasons.
  "backend.search.run.validation",
  // MSG-8a: pure compositions of other sentences (a join of clauses, a pair of references).
  "backend.history.assumption.opening",
  "backend.history.estimate.window_limited",
  "backend.history.reason.joined",
  "backend.history.settle.ticked",
  "common.product",
  "customRules.authoring.subjectKinds.LOG_SOURCE",
  "customRules.authoring.subjectKinds.WAF_ENDPOINT",
  "common.missing",
  "meta.title",
  "meta.titleTemplate",
  "shell.connection.ageSuffix",
  "shell.search.offenseId",
  "shell.search.shortcut",
  "users.authSources.LDAP",
  "account.english",
  "account.turkish",
  // I18N-3: QRadar object names and product terms the glossary keeps in English.
  "enums.anomalyState.NORMAL",
  "enums.sourceClass.DNS",
  "enums.sourceClass.IPS",
  "enums.sourceClass.LINUX",
  "enums.sourceClass.PROXY",
  "enums.sourceClass.VPN",
  "enums.sourceClass.WAF",
  "enums.sourceClass.WINDOWS",
  "home.healthGroups.normal",
  "pwchart.tooltip.point",
  "trend.groups.captionTime",
  "trend.single.timeOnlyAfter",
  "home.healthGroupsInline.normal",
  "home.offenses.offense",
  "units.eps",
  // I18N-11: QRadar's own object name stays English in Turkish (D-37).
  "propertyWatch.scopeKind.LOG_SOURCE",
  // I18N-11: a punctuation-only sentence terminator is the same in both languages.
  "propertyWatch.honesty.period",
  // I18N-5: punctuation around a placeholder, the same in both languages.
  "customRules.latency.tail",
  // I18N-10: "Normal" is the same word in Turkish; "Robust z" and "QRadar id" are technical terms
  // (the robust z-score, a QRadar object label) the glossary keeps in English.
  "ips.stateWord.NORMAL",
  "ips.detection.robustZ",
  "ips.editor.qradarId",
  "ips.table.qradarId",
  "ips.picker.qradarId",
  // I18N-9: the glossary keeps these product and technical terms English (WAF, endpoint, virtual
  // host, Normalizer, robust z); "QRadar id" matches QRadar's own screens; "WAF" alone is the
  // product name; "{label} (%)" is punctuation and a placeholder only.
  "waf.admin.picker.qradarId",
  "waf.admin.table.qradarId",
  "waf.chart.axisRatio",
  "waf.detection.robustZ",
  "waf.editor.qradarId",
  "waf.endpoints.endpoint",
  "waf.endpoints.normalizer",
  "waf.incident.virtualHost",
  "waf.sourcesWidget.linkWaf",
  "waf.state.NORMAL",
  "waf.traffic.linkWaf",
  // I18N-7: punctuation around a placeholder, and technical terms the glossary keeps in English.
  "cadence.latency.tail",
  "searches.list.mitre",
  "searches.editor.aql",
  "history.page.consoleLabel",
  "history.family.WAF",
  "history.family.IPS",
  "history.table.utc",
  "history.scope.logSource",
  // I18N-8: protocol and unit names the product keeps as QRadar and the directory show them
  // (StartTLS, TLS, Bind, the millisecond unit); the Turkish auth messages already say "bind".
  "directory.form.startTls",
  "directory.test.bind",
  "directory.test.tls",
  "directory.test.ms",
  // GUIDE-2: the guide's "Coverage" section title is the product term, English in both languages.
  "guide.sections.coverage.title",
  "guideLinks.titles.coverage",
]);

describe("message catalog contract", () => {
  const english = flatten(en);
  const turkish = flatten(tr);

  // D-41 (LANG-1): was "keeps exactly the same non-empty keys" (tr == en). Turkish is frozen at
  // the checkpoint list; an English key added since needs no Turkish twin.
  it("keeps every frozen Turkish key, adds none English lacks, and has no empty value", () => {
    expect(parityProblems(english, turkish)).toEqual([]);
    expect(Object.values(english).every((value) => value.length > 0)).toBe(true);
    expect(Object.values(turkish).every((value) => value.length > 0)).toBe(true);
  });

  it("parses every Turkish value as ICU and keeps the same argument names", () => {
    for (const key of Object.keys(turkish)) {
      expect(english[key], `${key} has no English twin`).toBeDefined();
      expect(() => parse(turkish[key]), key).not.toThrow();
      expect(argumentsOf(turkish[key]), key).toEqual(argumentsOf(english[key]));
    }
  });

  it("allows untranslated values only through the reviewed allowlist", () => {
    const identical = Object.keys(turkish).filter((key) => english[key] === turkish[key]);
    expect(identical.sort()).toEqual([...IDENTICAL_ALLOWLIST].sort());
  });
});

describe("frozen Turkish key list (D-41)", () => {
  const english = { "a.one": "One", "a.two": "Two", "b.three": "Three" };
  const turkish = { "a.one": "Bir", "a.two": "Iki", "b.three": "Uc" };
  const keys = ["a.one", "a.two", "b.three"];

  it("is the sorted, duplicate-free list of the checkpoint, and every key is in the catalog", () => {
    expect(frozen.count).toBe(FROZEN_TR_KEYS.length);
    expect(FROZEN_TR_KEYS).toEqual([...new Set(FROZEN_TR_KEYS)].sort());
    expect(frozen._comment).toContain("checkpoint/bilingual-2026-11-02");
    expect(FROZEN_TR_KEYS.length).toBeGreaterThan(6000);
  });

  it("accepts an English key with no Turkish twin while Turkish is not enabled", () => {
    expect(parityProblems({ ...english, "c.new": "New" }, turkish, ["en"], keys)).toEqual([]);
  });

  it("fails when a frozen Turkish key is deleted", () => {
    const { "a.two": _gone, ...rest } = turkish;
    expect(parityProblems(english, rest, ["en"], keys)).toEqual([
      "frozen Turkish key is missing: a.two",
    ]);
  });

  it("fails when Turkish has a key English lacks", () => {
    expect(parityProblems(english, { ...turkish, "z.only": "Sadece" }, ["en"], keys)).toEqual([
      "Turkish key has no English twin: z.only",
    ]);
  });

  it("asks for the Turkish twin of a new English key once Turkish is enabled", () => {
    expect(parityProblems({ ...english, "c.new": "New" }, turkish, ["en", "tr"], keys)).toEqual([
      "English key has no Turkish twin: c.new",
    ]);
  });
});
