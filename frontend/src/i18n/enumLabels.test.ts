import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import tr from "../../messages/tr";
import { enumLabel, statePillLabel, type EnumGroup, type EnumTranslator } from "./enumLabels";
import { rowTypeLabel } from "@/components/ips/ipsLabels";
import { createLocaleTranslator } from "./fallback";

function translator(locale: "en" | "tr"): EnumTranslator {
  const say = createLocaleTranslator(locale);
  return Object.assign((key: string) => say(`enums.${key}`), {
    has: (key: string) => say.has?.(`enums.${key}`) ?? false,
  });
}

const GROUPS: Record<EnumGroup, string[]> = {
  severity: ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"],
  severityPill: ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"],
  anomalyState: [
    "INSUFFICIENT_DATA",
    "NORMAL",
    "CANDIDATE",
    "OPEN",
    "RECOVERING",
    "RESOLVED",
    "SUPPRESSED",
  ],
  offenseStatus: ["OPEN", "HIDDEN", "CLOSED"],
  anomalyType: [
    "VOLUME_SPIKE", "VOLUME_DROP", "NO_EVENTS", "WAF_REQUEST_RATE_SPIKE",
    "WAF_CLIENT_CONCENTRATION_SPIKE", "WAF_BLOCK_RATIO_SPIKE", "WAF_ERROR_RATIO_SPIKE",
    "WAF_CLIENT_ERROR_RATIO_SPIKE", "WAF_NEW_ENDPOINT_ACCESS", "WAF_CLIENT_ENDPOINT_FANOUT_SPIKE",
    "IPS_PORT_RATE_SPIKE", "IPS_DESTINATION_RATE_SPIKE", "IPS_SIGNATURE_BURST",
    "IPS_NEW_DESTINATION_PORT", "IPS_SOURCE_CONCENTRATION_SPIKE", "IPS_PORT_FANOUT_SPIKE",
    "IPS_BLOCK_RATIO_SHIFT",
  ],
  sourceClass: [
    "WAF", "IPS", "FIREWALL", "PROXY", "VPN", "LINUX", "WINDOWS", "NETWORK", "DNS", "DATABASE",
    "EMAIL", "ENDPOINT", "CLOUD", "IDENTITY", "APPLICATION", "QRADAR_INTERNAL", "OTHER",
    "UNCLASSIFIED",
  ],
};

describe("enum labels", () => {
  it("has one non-empty label per value in both languages, and no value the table lacks", () => {
    for (const [group, values] of Object.entries(GROUPS)) {
      expect(Object.keys(en.enums[group as EnumGroup]).sort(), group).toEqual([...values].sort());
      expect(Object.keys(tr.enums[group as EnumGroup]).sort(), group).toEqual([...values].sort());
      for (const locale of ["en", "tr"] as const) {
        const t = translator(locale);
        for (const value of values) {
          expect(enumLabel(t, group as EnumGroup, value), `${locale} ${group}.${value}`).not.toBe("");
        }
      }
    }
  });

  it("looks the value up, so Turkish shows the Turkish word", () => {
    const t = translator("tr");
    expect(enumLabel(t, "severity", "CRITICAL")).toBe("Kritik");
    expect(enumLabel(t, "anomalyState", "RECOVERING")).toBe("DÜZELİYOR");
    expect(enumLabel(t, "offenseStatus", "CLOSED")).toBe("Kapalı");
    expect(enumLabel(t, "sourceClass", "FIREWALL")).toBe("Güvenlik duvarı");
  });

  it("shows an unknown value as it was sent, never an empty string", () => {
    for (const locale of ["en", "tr"] as const) {
      const t = translator(locale);
      expect(enumLabel(t, "severity", "SEVERE")).toBe("SEVERE");
      expect(enumLabel(t, "anomalyState", "")).toBe("");
      // A dot would read as a path into the catalog: such a value is never a key.
      expect(enumLabel(t, "sourceClass", "a.b")).toBe("a.b");
      expect(enumLabel(t, "anomalyType", "SOMETHING_NEW")).toBe("SOMETHING_NEW");
    }
  });

  it("shows an anomaly state pill as the base did in English, in capitals in Turkish", () => {
    // The base printed the backend's code in capitals: INSUFFICIENT_DATA, not a sentence.
    expect(statePillLabel(translator("en"), "INSUFFICIENT_DATA")).toBe("INSUFFICIENT_DATA");
    expect(statePillLabel(translator("en"), "open")).toBe("OPEN");
    expect(statePillLabel(translator("tr"), "RECOVERING")).toBe("DÜZELİYOR");
    expect(statePillLabel(translator("tr"), "INSUFFICIENT_DATA")).toBe("YETERSİZ VERİ");
    // A state the catalog lacks is its own code in capitals, never empty.
    expect(statePillLabel(translator("tr"), "paused")).toBe("PAUSED");
  });

  it("keeps the English severity pill and the English type names of the base", () => {
    expect(enumLabel(translator("en"), "severityPill", "HIGH")).toBe("HIGH");
    expect(enumLabel(translator("tr"), "severityPill", "INFO")).toBe("BİLGİ");
    expect(enumLabel(translator("en"), "anomalyType", "NO_EVENTS")).toBe("No events");
    expect(enumLabel(translator("tr"), "anomalyType", "VOLUME_DROP")).toBe("Hacim düşüşü");
  });

  it("names every type as the lists name it in English (the base's own tables)", () => {
    for (const type of GROUPS.anomalyType) {
      expect(enumLabel(translator("en"), "anomalyType", type), type).toBe(
        rowTypeLabel({ subject_kind: type.startsWith("IPS_") ? "IPS_ENTITY" : "LOG_SOURCE", anomaly_type: type } as never),
      );
    }
  });
});
