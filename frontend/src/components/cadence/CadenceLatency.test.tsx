import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { customRuleLatency } from "@/components/customrules/authoring";
import { pinAvailability, pwLatency } from "@/components/propertywatch/pwFormat";
import type { CallerInfo } from "@/lib/api";

import { CadenceLatency, canReadSearchCadence, delayText, partsText } from "./CadenceLatency";

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
    expect(text).toContain("This is not real-time correlation; for alerting within seconds");
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
