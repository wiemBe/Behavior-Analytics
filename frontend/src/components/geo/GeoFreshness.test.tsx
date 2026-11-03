// The Geography page's age line (GEO-5): the same judgement the widget's frame makes, with the
// GEO_COLLECTION cadence from the shared cadence read.

import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import en from "../../../messages/en";
import tr from "../../../messages/tr";
import { ENABLED_LOCALES } from "@/i18n/locale";
import { FROZEN_TR_KEYS, flattenCatalog, parityProblems } from "@/i18n/trFrozen";

import { GeoFreshness } from "./GeoFreshness";

const { reported, state } = vi.hoisted(() => ({
  reported: [] as Array<string | null>,
  state: { value: { status: "loading" } as unknown },
}));
vi.mock("@/components/shell/AutoRefresh", async (orig) => ({
  ...(await orig<typeof import("@/components/shell/AutoRefresh")>()),
  useReportDataAge: (iso: string | null) => void reported.push(iso),
}));
vi.mock("@/hooks/useApiResource", () => ({ useApiResource: () => state.value }));

const NOW = new Date("2026-11-02T12:00:00Z");
const at = (hoursAgo: number) => new Date(NOW.getTime() - hoursAgo * 3_600_000).toISOString();
const cadence = (seconds: number) => ({
  status: "ok",
  receivedAt: NOW.toISOString(),
  refreshing: false,
  refreshFailed: null,
  data: [{ family: "GEO_COLLECTION", cadence_seconds: seconds, bucket_seconds: 300 }],
});

beforeEach(() => {
  reported.length = 0;
  state.value = { status: "loading" };
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

describe("GeoFreshness", () => {
  it("shows where the data reaches in UTC, and tells the top bar", () => {
    render(<GeoFreshness asOf={at(1)} />);
    expect(screen.getByText(/Data to/)).toHaveTextContent("Data to 11:00:00 UTC");
    expect(screen.queryByText(/Stale:/)).toBeNull();
    expect(reported.at(-1)).toBe(at(1));
  });

  it("is stale past twice the unknown-cadence default (4 h) and says how old", () => {
    render(<GeoFreshness asOf={at(5)} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Stale: the latest data is from 07:00:00 UTC, 5 h old.",
    );
  });

  it("uses the GEO_COLLECTION cadence when the cadence read has it", () => {
    state.value = cadence(900);
    render(<GeoFreshness asOf={at(1)} />);
    expect(screen.getByRole("status")).toHaveTextContent("Stale: the latest data is from");
  });

  it("does not use another family's cadence", () => {
    state.value = {
      ...cadence(900),
      data: [{ family: "PHASE_A_VOLUME", cadence_seconds: 900, bucket_seconds: 300 }],
    };
    render(<GeoFreshness asOf={at(1)} />);
    expect(screen.queryByText(/Stale:/)).toBeNull();
  });

  it("says the time cannot be read rather than vouching for it", () => {
    render(<GeoFreshness asOf="not a time" />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Stale: the time the latest data reaches cannot be read.",
    );
  });

  it.each([null, undefined])("makes no claim and draws nothing for %s", (asOf) => {
    const { container } = render(<GeoFreshness asOf={asOf} />);
    expect(container).toBeEmptyDOMElement();
    expect(reported.at(-1)).toBeNull();
  });

  it("carries English-only sentences that a Turkish re-enable must translate first (D-41, GEO-7)", () => {
    // The four sentences moved from `home.frame` (frozen Turkish twins) to `geo.freshness`, added
    // after the checkpoint. Turkish is unreachable while ENABLED_LOCALES is English only; turning
    // it back on makes `parityProblems` demand these twins, so the line can never reach a Turkish
    // analyst as key paths. When the twins are added, assert /Güncel değil/ here again.
    const english = flattenCatalog(en);
    const keys = Object.keys(english).filter((key) => key.startsWith("geo.freshness."));
    expect(keys.sort()).toEqual([
      "geo.freshness.dataTo",
      "geo.freshness.dataToAge",
      "geo.freshness.stale",
      "geo.freshness.staleUnreadable",
    ]);
    for (const key of keys) expect(FROZEN_TR_KEYS).not.toContain(key);
    expect(ENABLED_LOCALES).toEqual(["en"]);
    const turkish = flattenCatalog(tr);
    expect(parityProblems(english, turkish, ["en", "tr"])).toEqual(
      expect.arrayContaining(keys.map((key) => `English key has no Turkish twin: ${key}`)),
    );
  });
});
