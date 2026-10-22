import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ResourceState } from "@/hooks/useApiResource";
import type { SearchCadenceItem } from "@/lib/api";
import { CadenceProvider } from "@/widgets/cadence";

import { WidgetFrame, type DataTo } from "./WidgetFrame";

const res = vi.hoisted(() => ({ cadence: { status: "loading" } as unknown }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: () => res.cadence as ResourceState<unknown>,
}));

// A note's text is split by its <time> element: match the paragraph's whole text.
const note = (re: RegExp) =>
  screen.getByText((_c, el) => el?.tagName === "P" && re.test(el.textContent ?? ""));

const NOW = Date.parse("2026-10-22T12:00:00Z");
const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();

const answer = (over: Partial<Extract<ResourceState<string>, { status: "ok" }>> = {}) =>
  ({
    status: "ok",
    data: "the figures",
    // The fetch time, deliberately different from every data time below.
    receivedAt: "2026-10-22T11:59:30Z",
    refreshing: false,
    refreshFailed: null,
    ...over,
  }) as ResourceState<string>;

function item(family: SearchCadenceItem["family"], cadence: number): SearchCadenceItem {
  return {
    family,
    cadence_seconds: cadence,
    allowed_cadences_seconds: [cadence],
    bucket_seconds: 300,
    settle_seconds: 0,
    last_slot_start: null,
    next_dispatch_at: "2026-10-22T12:05:00Z",
    updated_at: "2026-10-22T00:00:00Z",
    updated_by: null,
    last_run: null,
  };
}

function frame(state: ResourceState<string>, dataTo?: DataTo, variant: "panel" | "kpi" = "panel") {
  return render(
    <CadenceProvider>
      <WidgetFrame
        id="f"
        title="Fleet EPS"
        link={{ href: "/fleet", label: "Fleet" }}
        what="the fleet EPS"
        permission="read:fleet"
        state={state}
        dataTo={dataTo}
        variant={variant}
      >
        {({ data }) => <p>{data}</p>}
      </WidgetFrame>
    </CadenceProvider>,
  );
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  res.cadence = { status: "ok", data: [item("PHASE_A_VOLUME", 300), item("WAF_COLLECTION", 900)] };
});
afterEach(() => vi.useRealTimers());

describe("WidgetFrame data time", () => {
  it("shows the data's time, not the fetch time, with the age after it", () => {
    frame(answer(), { at: ago(240), family: "PHASE_A_VOLUME" });
    expect(screen.getByText("the figures")).toBeInTheDocument();
    expect(note(/^Data to/)).toHaveTextContent("Data to 11:56:00 UTC, 4 min ago.");
    expect(screen.queryByText(/11:59:30/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Stale/)).not.toBeInTheDocument();
  });

  it("stays ok at twice the family's cadence and turns stale just past it", () => {
    const view = frame(answer(), { at: ago(600), family: "PHASE_A_VOLUME" });
    expect(screen.queryByText(/Stale/)).not.toBeInTheDocument();
    view.unmount();
    frame(answer(), { at: ago(601), family: "PHASE_A_VOLUME" });
    expect(note(/Stale: the latest data is from 11:49:59 UTC, 10 min old\./)).toBeInTheDocument();
    // Stale data is still drawn.
    expect(screen.getByText("the figures")).toBeInTheDocument();
    expect(screen.queryByText(/Data to/)).not.toBeInTheDocument();
  });

  it("judges each widget against its own family's cadence", () => {
    // 20 min old: past 2 x 5 min (Phase A), inside 2 x 15 min (WAF).
    const view = frame(answer(), { at: ago(1200), family: "WAF_COLLECTION" });
    expect(screen.queryByText(/Stale/)).not.toBeInTheDocument();
    view.unmount();
    frame(answer(), { at: ago(1200), family: "PHASE_A_VOLUME" });
    expect(screen.getByText(/Stale:/)).toBeInTheDocument();
  });

  it.each([
    ["forbidden", { status: "forbidden", httpStatus: 403 }],
    ["failed", { status: "error", httpStatus: 500 }],
    ["still loading", { status: "loading" }],
  ])("falls back to the 2 h default when the cadence read is %s", (_name, cadence) => {
    res.cadence = cadence;
    // 3 h 59 min: inside the 4 h threshold; 5 min past it: stale. No family cadence is guessed.
    const view = frame(answer(), { at: ago(4 * 3600 - 60), family: "PHASE_A_VOLUME" });
    expect(screen.queryByText(/Stale/)).not.toBeInTheDocument();
    view.unmount();
    frame(answer(), { at: ago(4 * 3600 + 300), family: "PHASE_A_VOLUME" });
    expect(screen.getByText(/Stale:/)).toBeInTheDocument();
  });

  it("falls back to the default for a family the cadence read does not list", () => {
    res.cadence = { status: "ok", data: [item("WAF_COLLECTION", 900)] };
    frame(answer(), { at: ago(1200), family: "PHASE_A_VOLUME" });
    expect(screen.queryByText(/Stale/)).not.toBeInTheDocument();
  });

  it("calls an unreadable data time stale, with no age", () => {
    frame(answer(), { at: "not a time", family: "PHASE_A_VOLUME" });
    expect(screen.getByText("Stale: the time the latest data reaches cannot be read.")).toBeInTheDocument();
    expect(screen.getByText("the figures")).toBeInTheDocument();
  });

  it("never calls a widget stale whose response has no data time", () => {
    const view = frame(answer({ receivedAt: ago(30 * 86400) }));
    expect(screen.queryByText(/Stale|Data to/)).not.toBeInTheDocument();
    view.unmount();
    // The widget has a field but no value yet (no bucket stored): no time, no verdict.
    frame(answer({ receivedAt: ago(30 * 86400) }), { at: null, family: "PHASE_A_VOLUME" });
    expect(screen.queryByText(/Stale|Data to/)).not.toBeInTheDocument();
  });

  it("does not turn an error, an empty answer or a loading widget into stale", () => {
    const view = frame({ status: "error", httpStatus: 500 }, { at: ago(86400), family: "PHASE_A_VOLUME" });
    expect(screen.queryByText(/Stale/)).not.toBeInTheDocument();
    view.unmount();
    frame({ status: "loading" }, { at: ago(86400), family: "PHASE_A_VOLUME" });
    expect(screen.queryByText(/Stale/)).not.toBeInTheDocument();
  });

  it("names the data's time, not the fetch time, when a refresh failed", () => {
    frame(
      answer({ refreshFailed: { httpStatus: 503 } }),
      { at: ago(240), family: "PHASE_A_VOLUME" },
    );
    expect(note(/The latest refresh failed \(HTTP 503\)\. Showing the data to 11:56:00 UTC\./)).toBeInTheDocument();
  });

  it("keeps the received-at wording where the widget has no data time", () => {
    frame(answer({ refreshFailed: { httpStatus: 503 } }));
    expect(note(/Showing the data received at 11:59:30 UTC/)).toBeInTheDocument();
  });

  it("works in the KPI variant", () => {
    frame(answer(), { at: ago(240), family: "PHASE_A_VOLUME" }, "kpi");
    expect(note(/^Data to/)).toHaveClass("kpi-foot");
    const view = frame(answer(), { at: ago(5000), family: "PHASE_A_VOLUME" }, "kpi");
    expect(view.container.querySelector(".kpi-refresh-failed")).toHaveTextContent("Stale:");
  });

  it("leaves a stale state a widget gave itself untouched", () => {
    render(
      <WidgetFrame
        id="f"
        title="Property watch"
        link={{ href: "/behavior/watches", label: "Watches" }}
        what="the watch"
        permission="read:property_watches"
        state={{ kind: "stale", data: "drawn", asOf: ago(7200), ageSeconds: 7200 }}
      >
        {({ data }) => <p>{data}</p>}
      </WidgetFrame>,
    );
    expect(note(/Stale: the latest data is from 10:00:00 UTC, 2 h old\./)).toBeInTheDocument();
  });
});
