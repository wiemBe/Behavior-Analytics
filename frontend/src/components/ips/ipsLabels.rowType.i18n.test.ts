// `rowTypeLabel` names the detector of a row of any family in the interface language (I18N-13
// round 1): a Phase A or WAF row used to ignore the translator and read English.
import { describe, expect, it } from "vitest";

import { createLocaleTranslator } from "@/i18n/testTranslator";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { ipsListItem } from "./fixtures";
import { rowTypeLabel } from "./ipsLabels";

const turkish: BehaviorI18n = { t: createLocaleTranslator("tr"), locale: "tr" };
const english: BehaviorI18n = { t: createLocaleTranslator("en"), locale: "en" };
const row = (subject_kind: string, anomaly_type: string) =>
  ({ ...ipsListItem(), subject_kind, anomaly_type }) as never;

describe("rowTypeLabel with a translator", () => {
  it("names a Phase A row in Turkish", () => {
    expect(rowTypeLabel(row("LOG_SOURCE", "VOLUME_SPIKE"), turkish)).toBe("Hacim artışı");
  });

  it("names a WAF row in Turkish", () => {
    expect(rowTypeLabel(row("WAF_ENDPOINT", "WAF_BLOCK_RATIO_SPIKE"), turkish)).toBe("Engelleme oranı artışı");
  });

  it("names an IPS row in Turkish, and an unknown type as sent", () => {
    expect(rowTypeLabel(row("IPS_ENTITY", "IPS_SIGNATURE_BURST"), turkish)).toBe("İmza patlaması");
    expect(rowTypeLabel(row("LOG_SOURCE", "VOLUME_COLLAPSE"), turkish)).toBe("VOLUME_COLLAPSE");
  });

  it("keeps its English default without one, and in English with one", () => {
    expect(rowTypeLabel(row("LOG_SOURCE", "VOLUME_SPIKE"))).toBe("Volume spike");
    expect(rowTypeLabel(row("LOG_SOURCE", "VOLUME_SPIKE"), english)).toBe("Volume spike");
  });
});
