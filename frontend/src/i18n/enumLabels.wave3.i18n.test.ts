import { describe, expect, it } from "vitest";

import { createLocaleTranslator } from "./testTranslator";
import { enumLabel } from "./enumLabels";

const tr = createLocaleTranslator("tr");

const GROUPS = {
  alertStatus: ["OPEN", "ACKNOWLEDGED", "RESOLVED"],
  alertStatusPill: ["OPEN", "ACKNOWLEDGED", "RESOLVED"],
  notificationStatus: ["PENDING", "SENT", "FAILED", "DEAD_LETTER", "SUPPRESSED"],
  notificationChannel: ["TEAMS", "EMAIL", "SLACK", "WEBHOOK", "SYSLOG"],
  alertTransition: ["OPENED", "ACKNOWLEDGED", "RESOLVED"],
  ruleHealth: [
    "HEALTHY",
    "NEVER_OBSERVED",
    "INACTIVE",
    "NOISY",
    "DISABLED",
    "DEPENDENCY_DEGRADED",
    "INSUFFICIENT_DATA",
    "UNKNOWN",
  ],
} as const;

describe("wave-three enum labels", () => {
  it("has a Turkish label for every alert and rule-health value", () => {
    for (const [group, values] of Object.entries(GROUPS)) {
      for (const value of values) {
        expect(enumLabel(tr, group as keyof typeof GROUPS, value), `${group}.${value}`).not.toBe(value);
      }
    }
  });

  it("shows an unknown value raw, never blank", () => {
    expect(enumLabel(tr, "ruleHealth", "FUTURE_HEALTH")).toBe("FUTURE_HEALTH");
    expect(enumLabel(tr, "notificationStatus", "RETRYING")).toBe("RETRYING");
  });
});
