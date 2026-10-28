import { parse, TYPE, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import tr from "../../messages/tr";

interface Catalog {
  [key: string]: string | Catalog;
}

function flatten(catalog: Catalog, prefix = "", out: Record<string, string> = {}) {
  for (const [key, value] of Object.entries(catalog)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out[path] = value;
    else flatten(value, path, out);
  }
  return out;
}

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
  "common.product",
  "common.missing",
  "meta.title",
  "meta.titleTemplate",
  "shell.connection.ageSuffix",
  "shell.search.offenseId",
  "shell.search.shortcut",
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
  "widgets.configure.countWhat",
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
]);

describe("message catalog contract", () => {
  const english = flatten(en);
  const turkish = flatten(tr);

  it("keeps exactly the same non-empty keys", () => {
    expect(Object.keys(turkish).sort()).toEqual(Object.keys(english).sort());
    expect(Object.values(english).every((value) => value.length > 0)).toBe(true);
    expect(Object.values(turkish).every((value) => value.length > 0)).toBe(true);
  });

  it("parses every Turkish value as ICU and keeps the same argument names", () => {
    for (const key of Object.keys(english)) {
      expect(() => parse(turkish[key]), key).not.toThrow();
      expect(argumentsOf(turkish[key]), key).toEqual(argumentsOf(english[key]));
    }
  });

  it("allows untranslated values only through the reviewed allowlist", () => {
    const identical = Object.keys(english).filter((key) => english[key] === turkish[key]);
    expect(identical.sort()).toEqual([...IDENTICAL_ALLOWLIST].sort());
  });
});
