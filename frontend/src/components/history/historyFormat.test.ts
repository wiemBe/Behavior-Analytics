import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import {
  conflictConsole,
  estimateText,
  familyName,
  formatDays,
  modeText,
  questionText,
  relativeAge,
  stateInfo,
} from "./historyFormat";
import { consoleOf, estimateFamily } from "./fixtures";

describe("history backfill words", () => {
  it("names every console mode and says plainly what each one means", () => {
    expect(modeText("UNDECIDED")).toBe("Not decided");
    expect(modeText("NO")).toBe("No: history is not read");
    expect(modeText("ACTIVE")).toBe("Yes: history is read");
    expect(modeText("STOPPED")).toBe("Yes: reading was stopped");
    expect(modeText("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });

  it("gives each stored state its own words and shows an unknown one as recorded", () => {
    const labels = ["WAITING", "READING", "COMPLETE", "PARTIAL", "UNAVAILABLE", "NOT_ELIGIBLE"].map(
      (s) => stateInfo(s).label,
    );
    expect(new Set(labels).size).toBe(labels.length);
    expect(stateInfo("PARTIAL").tone).toBe("warn");
    expect(stateInfo("UNAVAILABLE").tone).toBe("neutral");
    expect(stateInfo("NEW_STATE")).toEqual({ label: "NEW_STATE", tone: "neutral" });
  });

  it("names the families and keeps an unknown one as recorded", () => {
    expect(familyName("PHASE_A")).toBe("Source volume");
    expect(familyName("CATCHALL")).toBe("Catch-all senders");
    expect(familyName("GEO")).toBe("GEO");
  });

  it("formats days read so that 0.0 stays 0.0", () => {
    expect(formatDays(9)).toBe("9");
    expect(formatDays(0)).toBe("0.0");
    expect(formatDays(0.2)).toBe("0.2");
    expect(formatDays(12.5)).toBe("12.5");
  });

  it("asks the question with the number it is given, or without one", () => {
    expect(questionText(28)).toBe("Read the last 28 days from QRadar?");
    expect(questionText(7)).toBe("Read the last 7 days from QRadar?");
    expect(questionText(null)).toBe("Read QRadar's past?");
  });

  it("gives a relative age only for a time in the past, in coarse units", () => {
    const now = new Date("2026-10-20T12:00:00Z");
    expect(relativeAge("2026-10-20T11:48:00Z", now)).toBe("12 min ago");
    expect(relativeAge("2026-10-20T06:00:00Z", now)).toBe("6 h ago");
    expect(relativeAge("2026-10-10T12:00:00Z", now)).toBe("10 d ago");
    expect(relativeAge("2026-10-21T12:00:00Z", now)).toBe("");
    expect(relativeAge(null, now)).toBe("");
  });

  it("never turns a missing estimate into a zero", () => {
    const missing = estimateText(
      estimateFamily({ searches: null, hours: null, reason: "no estimate: switched off" }),
    );
    expect(missing).toEqual({ figures: "—", note: "no estimate: switched off" });
    const untimed = estimateText(estimateFamily({ hours: null }));
    expect(untimed.figures).toBe("5 searches, time not estimated");
    expect(estimateText(undefined).figures).toBe("—");
  });

  it("reads the console out of a 409 body and nothing else", () => {
    const c = consoleOf();
    expect(conflictConsole(new ApiError(409, "x", "x", { detail: "x", console: c }))).toBe(c);
    expect(conflictConsole(new ApiError(409, "x", "x", { detail: "x", console: null }))).toBeNull();
    expect(conflictConsole(new ApiError(500, "x", "x", { console: c }))).toBeNull();
    expect(conflictConsole(new Error("x"))).toBeNull();
  });
});
