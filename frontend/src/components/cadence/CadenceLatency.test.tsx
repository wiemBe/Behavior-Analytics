import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { customRuleLatency, customRuleLatencyFor } from "@/components/customrules/authoring";
import { pinAvailability, pwLatency } from "@/components/propertywatch/pwFormat";
import type { CallerInfo } from "@/lib/api";

import { catalog } from "@/components/customrules/fixtures";
import type { Cadence } from "@/lib/api";

import {
  CadenceLatency,
  canReadSearchCadence,
  delayText,
  latencyCadence,
  partsText,
} from "./CadenceLatency";

const api = (family: Cadence["family"], cadence: number, bucket: number | null = 300, settle = 120): Cadence => ({
  family,
  cadence_seconds: cadence,
  bucket_seconds: bucket,
  settle_seconds: settle,
});

const at = (cadenceSeconds: number) => ({ cadenceSeconds, bucketSeconds: 300, settleSeconds: 120 });

function caller(permissions: string[], effective: string[] = []): CallerInfo {
  return { subject: "x", email: null, permissions, effective_permissions: effective };
}

describe("the delay figure", () => {
  it.each([
    [300, "up to 12 min"],
    [900, "up to 22 min"],
    [28800, "up to 8 h 7 min"],
  ])("is the helper's worst case for a %i s cadence", (seconds, text) => {
    expect(delayText(at(seconds))).toBe(text);
  });

  it("is no number when the cadence is not known", () => {
    expect(delayText(null)).toBeNull();
    expect(delayText(undefined)).toBeNull();
    expect(delayText({ cadenceSeconds: Number.NaN, bucketSeconds: 300, settleSeconds: 120 })).toBeNull();
  });
});

describe("the custom-rule sentence", () => {
  it.each([
    [300, "up to 12 min after the events arrive"],
    [900, "up to 22 min after the events arrive"],
    [28800, "up to 8 h 7 min after the events arrive"],
  ])("states the computed delay at a %i s cadence", (seconds, phrase) => {
    const text = partsText(customRuleLatency(at(seconds)));
    expect(text).toContain(`collected 5-minute buckets, ${phrase}`);
    expect(text).toContain("This is not a seconds-level correlation engine");
    expect(text).not.toMatch(/about an hour/);
  });

  it("gives no number when the cadence is unknown, and says what the delay depends on", () => {
    const text = partsText(customRuleLatency());
    expect(text).toContain("after each collection of their family");
    expect(text).toContain("the schedule is set on Search cadence");
    expect(text).not.toMatch(/\d+ ?(min|h)\b[^-]*after the events/);
    expect(text).not.toMatch(/\bhour\b|real-time/);
  });
});

describe("the property-watch sentence", () => {
  it.each([
    [300, "up to 12 min after the events arrive"],
    [900, "up to 22 min after the events arrive"],
    [28800, "up to 8 h 7 min after the events arrive"],
  ])("states the computed delay at a %i s cadence", (seconds, phrase) => {
    const text = partsText(pwLatency(at(seconds)));
    expect(text).toContain(`Watches are judged on collected 5-minute buckets, ${phrase}`);
    expect(text).toContain("This is not a seconds-level correlation engine — for alerting within seconds");
  });

  it("gives no number when the cadence is unknown", () => {
    const text = partsText(pwLatency(null));
    expect(text).toContain("after each collection of their family");
    expect(text).not.toMatch(/up to|\bhour\b/);
  });
});

describe("the pin sentence", () => {
  it("promises no number without the cadence", () => {
    expect(pinAvailability()).toBe("after the next property-watch collection");
  });

  it.each([
    [300, "up to 12 min"],
    [900, "up to 22 min"],
    [28800, "up to 8 h 7 min"],
  ])("adds the computed delay at a %i s cadence", (seconds, phrase) => {
    expect(pinAvailability(at(seconds))).toBe(
      `after the next property-watch collection, which runs ${phrase} after the events arrive`,
    );
  });
});

describe("the link to Search cadence", () => {
  it("is present for a reader of read:search_cadence", () => {
    render(<CadenceLatency className="x" parts={customRuleLatency()} canLink />);
    expect(screen.getByRole("link", { name: "Search cadence" })).toHaveAttribute(
      "href",
      "/admin/search-cadence",
    );
  });

  it("is absent without it, and the words stay", () => {
    render(<CadenceLatency className="x" parts={pwLatency()} canLink={false} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText(/the schedule is set on Search cadence\)\./)).toBeInTheDocument();
  });

  it("is judged from the grants, wildcards included, and fails closed", () => {
    expect(canReadSearchCadence(caller([], ["read:search_cadence"]))).toBe(true);
    expect(canReadSearchCadence(caller(["read:*"]))).toBe(true);
    expect(canReadSearchCadence(caller(["admin:*"]))).toBe(true);
    expect(canReadSearchCadence(caller(["custom_rule:write"], ["read:custom_rules"]))).toBe(false);
    expect(canReadSearchCadence(null)).toBe(false);
  });
});

describe("the cadence as the API sends it", () => {
  it("becomes the figure from its own cadence, bucket and settle time", () => {
    expect(delayText(latencyCadence(api("PROPERTY_WATCH", 3600, 300, 120)))).toBe("up to 1 h 7 min");
    expect(delayText(latencyCadence(api("PHASE_A_VOLUME", 900, 300, 600)))).toBe("up to 30 min");
  });

  it("is no figure when the API sent none", () => {
    expect(latencyCadence(undefined)).toBeNull();
    expect(latencyCadence(null)).toBeNull();
  });

  it("says the figure bounds evidence, not detection, for a family with no bucket", () => {
    expect(delayText(latencyCadence(api("ANOMALY_EXPLANATION", 900, null, 120)))).toBe("evidence up to 17 min");
    expect(partsText(pwLatency(latencyCadence(api("ANOMALY_EXPLANATION", 900, null, 120))))).toContain(
      "evidence up to 17 min after the events arrive",
    );
  });
});

describe("the custom-rule sentence for a catalog", () => {
  const kinds = [
    { subject_kind: "LOG_SOURCE" as const, column: "log_source_id", search_family: "PHASE_A_VOLUME" as const },
    { subject_kind: "FLEET_CLASS" as const, column: "source_class", search_family: "PHASE_A_VOLUME" as const },
    { subject_kind: "PW_VALUE" as const, column: "pw_value_id", search_family: "PROPERTY_WATCH" as const },
  ];

  it("says one figure once when every kind has the same", () => {
    const text = partsText(
      customRuleLatencyFor(
        catalog({
          subject_kinds: kinds,
          cadences: [api("PHASE_A_VOLUME", 900), api("PROPERTY_WATCH", 900)],
        }),
      ),
    );
    expect(text).toContain("collected 5-minute buckets, up to 22 min after the events arrive");
  });

  it("names each figure with its kinds when the cadences differ", () => {
    const text = partsText(
      customRuleLatencyFor(
        catalog({
          subject_kinds: kinds,
          cadences: [api("PHASE_A_VOLUME", 900), api("PROPERTY_WATCH", 3600, 300, 600)],
        }),
      ),
    );
    expect(text).toContain("Log source, Fleet class: up to 22 min; Property watch value: up to 1 h 15 min");
    expect(text).toContain("the schedule is set on Search cadence");
  });

  it("is the figure-free sentence when a kind's family has no cadence, never a guess", () => {
    const text = partsText(
      customRuleLatencyFor(catalog({ subject_kinds: kinds, cadences: [api("PHASE_A_VOLUME", 900)] })),
    );
    expect(text).toBe(partsText(customRuleLatency()));
  });

  it("is the figure-free sentence when the catalog carries no cadences or no family", () => {
    expect(partsText(customRuleLatencyFor(catalog()))).toBe(partsText(customRuleLatency()));
    expect(partsText(customRuleLatencyFor(null))).toBe(partsText(customRuleLatency()));
    expect(
      partsText(customRuleLatencyFor(catalog({ cadences: [api("PHASE_A_VOLUME", 900)] }))),
    ).toBe(partsText(customRuleLatency()));
  });
});
