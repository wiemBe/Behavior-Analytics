// A value the backend sends is never a catalog key. next-intl's `has()` and a plain `table[key]`
// follow the prototype chain, so "constructor" (or "toString") would be found and printed as copy.
// One test per helper that looks a raw value up; each runs in Turkish, where a hit would show.
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { setTestLocale } from "../../vitest.setup";
import { Evidence } from "@/components/Evidence";
import { outageModeLabel } from "@/components/catalog/OutagePolicy";
import { englishCatalogText } from "@/components/catalog/catalogText";
import { familyName } from "@/components/catalog/derive";
import { previewTooltip } from "@/components/customrules/PreviewChart";
import { metricLabel, operatorWord, statusLabel as ruleStatusLabel } from "@/components/customrules/authoring";
import { detectorSpec, ofText, quantityText } from "@/components/ips/ipsDetectors";
import { typeLabel } from "@/components/rules/ruleStatistics";
import { dependencyWord } from "@/components/rules/dependencyWord";
import { runReadout } from "@/components/searches/authoring";
import { buildSignals } from "@/components/shell/ConnectionStatus";
import { validationView } from "@/components/waf/wafSourceHealth";
import { qradarStatusLabel } from "@/app/log-sources/qradarStatus";
import { statusLabel as catchallStatusLabel } from "@/app/fleet/catchall/catchall";
import { createLocaleTranslator } from "@/i18n/testTranslator";
import { isPlainKey, own } from "@/i18n/ownKey";
import { wordOrValue } from "@/lib/behaviorI18n";
import { statusLabel as coverageStatusLabel } from "@/lib/coverageText";
import { basisLabel, classLabel } from "@/lib/fleet";
import { fleetTextOf } from "@/lib/fleetText";
import { enumLabel } from "@/i18n/enumLabels";

const tr = createLocaleTranslator("tr");
const i18n = { t: tr, locale: "tr" as const };
const fleet = fleetTextOf(tr, "tr");
const RAW = ["constructor", "toString", "__proto__", "hasOwnProperty"];

beforeEach(() => setTestLocale("tr"));

describe("the key guards", () => {
  it("accept a code and refuse an inherited name, a path and an empty value", () => {
    expect(isPlainKey("OPEN")).toBe(true);
    for (const raw of [...RAW, "a.b", ""]) expect(isPlainKey(raw), raw).toBe(false);
    expect(own({ a: 1 }, "a")).toBe(1);
    for (const raw of RAW) expect(own({ a: 1 }, raw), raw).toBeUndefined();
  });
});

describe("a raw backend value is shown as sent, never as a catalog lookup", () => {
  it("wordOrValue (behavior, history, anomaly types)", () => {
    for (const raw of RAW) expect(wordOrValue(i18n, "enums.anomalyType", raw)).toBe(raw);
  });

  it("enumLabel", () => {
    for (const raw of RAW) expect(enumLabel(tr, "severity", raw)).toBe(raw);
  });

  it("qradarStatusLabel", () => {
    for (const raw of RAW) expect(qradarStatusLabel(raw, tr)).toBe(raw);
  });

  it("the catch-all review status", () => {
    for (const raw of RAW) expect(catchallStatusLabel(raw as never, fleet)).toBe(raw);
  });

  it("the rule dependency words", () => {
    for (const raw of RAW) {
      expect(dependencyWord(tr, "mappingSources", raw)).toBe(raw);
      expect(dependencyWord(tr, "dependencyKinds", raw)).toBe(raw);
    }
  });

  it("the outage mode", () => {
    for (const raw of RAW) expect(outageModeLabel(fleet, raw as never)).toBeNull();
    expect(outageModeLabel(fleet, "UNKNOWN_MODE" as never)).toBeNull();
  });

  it("the search family name", () => {
    for (const raw of RAW) expect(familyName(raw, fleet)).toBe(familyName(raw, englishCatalogText));
    for (const raw of RAW) expect(familyName(raw, fleet)).not.toMatch(/function|\[object/i);
  });

  it("the fleet class and basis words", () => {
    for (const raw of RAW) {
      expect(classLabel(raw, fleet)).toBe(raw);
      expect(basisLabel(raw, fleet)).toBe(raw);
    }
  });

  it("the coverage status", () => {
    for (const raw of RAW) expect(coverageStatusLabel(tr, raw)).toBe(raw.replace(/_/g, " "));
  });

  it("the custom-rule metric, operator and status words", () => {
    for (const raw of RAW) {
      expect(metricLabel(raw, tr)).toBe(raw);
      expect(operatorWord(raw, tr)).toBe(raw);
      expect(ruleStatusLabel(raw, tr)).toBe(raw);
    }
  });

  it("the custom-rule preview completeness", () => {
    const preview = {
      unit: "events",
      buckets: [
        {
          bucket_start: "2026-10-27T10:00:00Z",
          measurement: "MEASURED",
          value: 3,
          completeness: "constructor",
        },
      ],
    };
    const html = previewTooltip(preview as never, "x", 0, "tr", tr);
    expect(html).toContain("constructor");
    expect(html).not.toMatch(/function|\[object/i);
  });

  it("the IPS detector specs", () => {
    for (const raw of RAW) {
      expect(detectorSpec(raw)).toBeNull();
      expect(quantityText(raw, i18n)).toBe("");
      expect(ofText(raw, i18n)).toBeNull();
    }
  });

  it("the WAF validation status", () => {
    for (const raw of RAW) {
      const view = validationView({ validation_status: raw, validation_current: true } as never, i18n);
      expect(view.label).toBe(raw);
    }
  });

  it("the rule type", () => {
    for (const raw of RAW) expect(typeLabel(raw, tr)).not.toMatch(/function|\[object/i);
    expect(typeLabel("constructor", tr)).toBe("Constructor");
  });

  it("the search run error type", () => {
    for (const raw of RAW) {
      const readout = runReadout({ status: "FAILED", error_type: raw, truncated: false } as never, 1, tr, "tr");
      expect(readout.text).toContain(raw);
      expect(readout.text).not.toMatch(/function|\[object/i);
    }
  });

  it("the collection outcome and instance status in the connection status", () => {
    for (const raw of RAW) {
      const signals = buildSignals(
        { state: "loading" },
        {
          state: "ok",
          data: { instance_status: raw, instance_last_health_check_at: null, instance_version: null },
        } as never,
        {
          state: "ok",
          data: [{ family: "PHASE_A_VOLUME", last_run: { outcome: raw, at: "2026-10-27T10:00:00Z" } }],
        } as never,
        Date.parse("2026-10-27T10:05:00Z"),
        tr,
      );
      const text = JSON.stringify(signals.map((s) => s.value));
      expect(text, raw).not.toMatch(/function|\[object/i);
      expect(signals.find((s) => s.key === "instance")?.tone).toBe("neutral");
    }
  });

  it("the detector spec of an evidence table", () => {
    render(<Evidence data={{ anomaly_type: "constructor", observed_value: 3 }} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
  });
});
