import { describe, expect, it } from "vitest";

import { createTranslator } from "next-intl";

import en from "../../../messages/en";
import { asTextTranslator } from "@/i18n/textTranslator";

import { callerLabel } from "./callers";

// The translator the component gets from `useTranslations("siemLoad.caller")`.
const t = asTextTranslator(
  createTranslator({ locale: "en", messages: en, namespace: "siemLoad.caller" }),
);
const say = (code: string) => callerLabel(t, code);

// The closed set of backend callers (`CALLERS` in backend/app/models/ariel_search.py).
const CALLERS = [
  "LOG_SOURCE_VOLUME",
  "ANOMALY_EXPLANATION",
  "RULE_MATCH",
  "WAF_COLLECTION",
  "IPS_COLLECTION",
  "PROPERTY_WATCH",
  "CATCHALL_SENDERS",
  "GEO_COLLECTION",
  "HISTORY",
  "PREVIEW",
  "SCHEDULED_SEARCH",
  "MANUAL_RUN",
  "QRADAR_HEALTH",
];

describe("callerLabel", () => {
  it("names a code in readable English", () => {
    expect(say("LOG_SOURCE_VOLUME")).toBe("Log source volume");
    expect(say("WAF_COLLECTION")).toBe("WAF collection");
  });

  it("has a readable label for every caller the backend knows, none of them the bare code", () => {
    for (const code of CALLERS) {
      const label = say(code);
      expect(label, code).not.toBe(code);
      expect(label, code).not.toMatch(/_/);
      expect(label, code).not.toBe("");
    }
  });

  it("renders a code it does not know as itself, never blank", () => {
    expect(say("NEW_CALLER")).toBe("NEW_CALLER");
    expect(say("lowercase")).toBe("lowercase");
  });

  it("does not turn a prototype name or a dotted code into interface copy", () => {
    expect(say("constructor")).toBe("constructor");
    expect(say("toString")).toBe("toString");
    expect(say("a.b")).toBe("a.b");
    expect(say("")).toBe("");
  });
});
