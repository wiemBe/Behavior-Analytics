import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HistoryShade } from "@/components/behavior/historyOrigin";
import { TriagePanel } from "@/components/triage/TriagePanel";
import type { Triage, TriageEvent } from "@/lib/api";
import { eventSentence } from "@/lib/triage";

import { ExclusionBandText, effectiveSpans } from "./ExclusionBandText";
import { exclusionMarkAreas } from "./exclusionBand";
import { exclusion } from "./fixtures";

const shade: HistoryShade = { color: "#111", border: "#222", text: "#333", font: "sans" };

describe("the chart band", () => {
  it("shades only an effective exclusion: pending or applied, not revoked or outside the lookback", () => {
    const spans = effectiveSpans([
      exclusion({ id: "p" }),
      exclusion({ id: "a", state: "APPLIED", starts_at: "2026-11-03T08:00:00Z", ends_at: "2026-11-03T09:00:00Z" }),
      exclusion({ id: "o", state: "OUTSIDE_LOOKBACK" }),
      exclusion({ id: "r", state: "REVOKED" }),
    ]);
    expect(spans).toEqual([
      { start: "2026-11-04T08:00:00Z", end: "2026-11-04T10:00:00Z" },
      { start: "2026-11-03T08:00:00Z", end: "2026-11-03T09:00:00Z" },
    ]);
    expect(effectiveSpans(null)).toEqual([]);
  });

  it("is a neutral, outlined area that carries its own words, labelled once on the widest span", () => {
    const areas = exclusionMarkAreas(
      [
        { start: 0, end: 10 },
        { start: 20, end: 100 },
      ],
      shade,
      "Excluded from baseline training",
    );
    expect(areas).toHaveLength(2);
    expect(areas[0][0].itemStyle).toEqual({ color: "#111", borderColor: "#222", borderWidth: 1, borderType: "solid" });
    expect(areas[0][0].label.show).toBe(false);
    expect(areas[1][0].label).toMatchObject({ show: true, formatter: "Excluded from baseline training" });
    expect(areas[1][1]).toEqual({ xAxis: 100 });
  });
});

describe("the text beside the band", () => {
  it("repeats the UTC range and the effect, so colour is not the only carrier", () => {
    render(<ExclusionBandText rows={[exclusion(), exclusion({ id: "x", state: "APPLIED", starts_at: "2026-11-03T08:00:00Z", ends_at: "2026-11-03T09:00:00Z" })]} />);
    const text = screen.getByText(/Excluded from baseline training:/);
    expect(text).toHaveTextContent("04 Nov 08:00–10:00 UTC (Pending next baseline rebuild)");
    expect(text).toHaveTextContent("03 Nov 08:00–09:00 UTC (Applied by a baseline rebuild)");
  });

  it("draws nothing when no exclusion is effective, or the rows could not be read", () => {
    const { container, rerender } = render(<ExclusionBandText rows={[exclusion({ state: "REVOKED" })]} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ExclusionBandText rows={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("the activity log", () => {
  const words = { status: (v: string) => v, disposition: (v: string) => v, unassigned: "none" };
  const say = (key: string) =>
    ({
      "events.baselineExclusionCreated": "Interval excluded from baseline training",
      "events.baselineExclusionRevoked": "Training exclusion revoked",
    })[key] ?? key;
  function event(kind: TriageEvent["kind"]): TriageEvent {
    return {
      id: "e",
      kind,
      actor: "k.osei",
      created_at: "2026-11-04T10:30:00Z",
      from_value: null,
      to_value: "33333333-3333-3333-3333-333333333333",
      comment: "Collector restarted",
      triage_version: 1,
    };
  }

  it("words both new event kinds in English, without the exclusion id", () => {
    expect(eventSentence(event("BASELINE_EXCLUSION_CREATED"), words, say)).toBe(
      "Interval excluded from baseline training",
    );
    expect(eventSentence(event("BASELINE_EXCLUSION_REVOKED"), words, say)).toBe("Training exclusion revoked");
  });

  function triage(events: TriageEvent[]): Triage {
    return {
      family: "log_source",
      anomaly_id: "a",
      status: "NEW",
      owner: null,
      disposition: null,
      disposition_note: null,
      updated_by: null,
      updated_at: null,
      version: 1,
      events: { items: events, total: events.length, limit: 50, offset: 0 },
    };
  }

  it("the panel renders the labels from the catalog, with the reason beneath, and its extra slot", () => {
    render(
      <TriagePanel
        family="log_source"
        anomalyId="a"
        initial={triage([event("BASELINE_EXCLUSION_CREATED"), event("BASELINE_EXCLUSION_REVOKED")])}
        access="read"
        me={null}
        extra={<p>slot content</p>}
      />,
    );
    expect(screen.getByText("Interval excluded from baseline training")).toBeInTheDocument();
    expect(screen.getByText("Training exclusion revoked")).toBeInTheDocument();
    expect(screen.getAllByText("Collector restarted")).toHaveLength(2);
    expect(screen.queryByText(/33333333/)).toBeNull();
    expect(screen.getByText("slot content")).toBeInTheDocument();
  });

  it("still shows the slot when the triage itself could not be loaded", () => {
    render(<TriagePanel family="log_source" anomalyId="a" initial={null} access="read" me={null} extra={<p>slot content</p>} />);
    expect(screen.getByText("slot content")).toBeInTheDocument();
  });
});
