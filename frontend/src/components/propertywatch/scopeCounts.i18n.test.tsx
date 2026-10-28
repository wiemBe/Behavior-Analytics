// Property-watch scope counts and the scope search label in Turkish (I18N-12): the count is
// grouped the Turkish way (`1.204 kaynak`) wherever a scope's member count is shown, and the
// search label opens with a fixed word, not a lowercase noun.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { createLocaleTranslator } from "@/i18n/testTranslator";
import { api } from "@/lib/api";

import { watchSummary, type WatchDraft } from "./authoring";
import { WatchScopePicker } from "./WatchPickers";

beforeEach(() => setTestLocale("tr"));
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const draft = (scopeCount: number | null): WatchDraft => ({
  name: "w",
  description: "",
  scopeKind: "LOG_SOURCE_TYPE",
  scopeId: "7",
  scopeLabel: "Firewalls",
  scopeCount,
  properties: [{ kind: "NORMALIZED", name: "username" }],
  pins: [],
  detectors: [],
  severity: "MEDIUM",
  historyDays: 0,
  historyTouched: false,
});

describe("property-watch scope counts in Turkish", () => {
  it("groups the member count in the summary and leaves it out when unknown", () => {
    const i18n = { t: createLocaleTranslator("tr"), locale: "tr" as const };
    expect(watchSummary(draft(1204), i18n)).toContain("Firewalls (1.204 kaynak)");
    expect(watchSummary(draft(null), i18n)).not.toContain("kaynak)");
  });

  it("groups the count in the option text and the listed total, and opens the label with a fixed word", async () => {
    vi.useFakeTimers();
    vi.spyOn(api, "propertyWatchScopeOptions").mockResolvedValue([
      {
        kind: "LOG_SOURCE_TYPE",
        id: "1",
        qradar_id: 1,
        name: "Firewalls",
        detail: "",
        synced_source_count: 1204,
        scope_state: null,
        scope_reason: null,
      },
    ]);
    render(<WatchScopePicker kind="LOG_SOURCE_TYPE" selectedId="" onKind={vi.fn()} onSelect={vi.fn()} />);
    const filter = screen.getByRole("searchbox", { name: /^Bul: / });
    expect(filter.getAttribute("id")).toBeTruthy();
    fireEvent.change(filter, { target: { value: "F" } });
    await act(async () => vi.advanceTimersByTime(200));
    expect(screen.getByRole("option", { name: /Firewalls — 1\.204 kaynak/ })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Listelenen");
    expect(screen.queryByRole("option", { name: /1204/ })).toBeNull();
  });
});
