import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HistoryText, OriginMarker } from "./HistoryKey";
import {
  HISTORY_TEXT,
  historyIntervals,
  historySummary,
  originCounts,
  originText,
  type OriginRow,
} from "./historyOrigin";

const row = (m: number, origin: OriginRow["origin"]): OriginRow => ({
  bucket_start: new Date(Date.UTC(2026, 9, 12, 9, m)).toISOString(),
  bucket_seconds: 300,
  origin,
});

describe("historyIntervals", () => {
  it("merges contiguous history buckets and breaks at a live or unknown one", () => {
    const spans = historyIntervals([
      row(15, "HISTORY"),
      row(0, "HISTORY"),
      row(5, "HISTORY"),
      row(10, "LIVE"),
      row(20, "HISTORY"),
      row(25, null),
    ]);
    expect(spans.map((s) => [s.start, s.end, s.buckets])).toEqual([
      [Date.UTC(2026, 9, 12, 9, 0), Date.UTC(2026, 9, 12, 9, 10), 2],
      [Date.UTC(2026, 9, 12, 9, 15), Date.UTC(2026, 9, 12, 9, 25), 2],
    ]);
  });

  it("finds nothing in a live series, in one with unknown origins, or in a response with no origin", () => {
    expect(historyIntervals([row(0, "LIVE"), row(5, null), row(10, undefined)])).toEqual([]);
  });
});

describe("the words", () => {
  it("says a history bucket was read from QRadar history, not judged", () => {
    expect(originText("HISTORY")).toBe("Read from QRadar history, not judged");
  });

  it("says an unknown origin is unknown, and never calls it live", () => {
    expect(originText(null)).toMatch(/origin unknown/);
    expect(originText(null)).not.toMatch(/live/i);
  });

  it("says nothing for a live bucket or a response with no origin", () => {
    expect(originText("LIVE")).toBe("");
    expect(originText(undefined)).toBe("");
  });

  it("counts history and unknown buckets separately", () => {
    expect(originCounts([row(0, "HISTORY"), row(5, null), row(10, "LIVE")])).toEqual({
      total: 3,
      history: 1,
      unknown: 1,
    });
  });
});

describe("historySummary (assistive technology)", () => {
  it("names the count, the words and the UTC span", () => {
    const text = historySummary([row(0, "HISTORY"), row(5, "HISTORY"), row(10, "LIVE")]);
    expect(text).toContain("2 of 3 buckets were " + HISTORY_TEXT);
    expect(text).toContain("2026-10-12 09:00 to 2026-10-12 09:10 UTC");
  });

  it("names unknown origins", () => {
    expect(historySummary([row(0, null), row(5, "LIVE")])).toMatch(/1 bucket has an origin unknown/);
  });

  it("is empty, and renders nothing, for a series without either", () => {
    expect(historySummary([row(0, "LIVE")])).toBe("");
    const { container } = render(<HistoryText rows={[row(0, "LIVE")]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("OriginMarker", () => {
  it("marks a history row and an unknown row, and a live row not at all", () => {
    const { container, rerender } = render(<OriginMarker origin="HISTORY" />);
    expect(screen.getByText(HISTORY_TEXT)).toBeInTheDocument();
    rerender(<OriginMarker origin={null} />);
    expect(screen.getByText("origin unknown")).toBeInTheDocument();
    rerender(<OriginMarker origin="LIVE" />);
    expect(container).toBeEmptyDOMElement();
  });
});
