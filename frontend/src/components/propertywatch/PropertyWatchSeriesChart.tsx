"use client";

// The series chart of a property watch: events per bucket for up to five values at once, with the
// parsed share of the scope's events beneath it on the same UTC time axis.
//
// It is shared on purpose. The watch page draws it from stored series; PW-8's preview and PW-9's
// dashboard widget feed it the same props (`series`, the window, `collected`, optional `coverage`
// and `incidents`), and `compact` drops the coverage panel for a widget body. It reads nothing
// itself: every number arrives as a prop, so a caller decides where its data comes from.
//
// The honesty rules are those of `pwSeries.ts` and `pwChartOption.ts`, each pinned by a test:
// a gap is a gap, a measured zero is 0, a lower bound is "at least n", the expected range is one
// band and only for one value, and the colours are the five categorical tokens.

import { LineChart } from "echarts/charts";
import {
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef } from "react";

import type { PwBucket, PwValueSeries } from "@/lib/api";
import { readChartPalette } from "@/lib/chartPalette";

import "./propertywatch.css";
import {
  buildCoverage,
  buildPwOption,
  PW_CATEGORY_TOKENS,
  type DetectionBand,
  type PwPalette,
} from "./pwChartOption";
import { eventsUnit } from "./pwFormat";
import { buildPlot, PW_MAX_SERIES } from "./pwSeries";

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  MarkAreaComponent,
  MarkLineComponent,
  CanvasRenderer,
]);

/** The palette from the tokens. Browser-only: call it from an effect. */
export function readPwPalette(root: Element = document.documentElement): PwPalette {
  const base = readChartPalette(root);
  const style = getComputedStyle(root);
  return {
    cat: PW_CATEGORY_TOKENS.map((name) => style.getPropertyValue(name).trim()),
    grid: base.grid,
    axis: base.axis,
    label: base.label,
    band: base.band,
    gap: base.gap,
    anomaly: base.anomaly,
    marker: base.marker,
    expected: base.expected,
    tooltipBg: base.tooltipBg,
    tooltipBorder: base.tooltipBorder,
    tooltipText: base.tooltipText,
    font: base.font,
  };
}

export interface PropertyWatchSeriesChartProps {
  /** The values to draw. More than five are not drawn, and the chart says so. */
  series: PwValueSeries[];
  bucketSeconds: number;
  /** The window the axis covers (ISO). Omitted: the extent of the points. */
  windowStart?: string | null;
  windowEnd?: string | null;
  /** Bucket starts that were collected (ISO). Omitted: taken from the series' points. */
  collected?: string[] | null;
  /**
   * The stored buckets, for the parsed-share panel, and `pw_min_coverage` as a fraction for its
   * floor line. Omitted: no coverage panel.
   */
  coverage?: { buckets: PwBucket[]; floor: number | null } | null;
  incidents?: Array<{ start: string; end: string | null }>;
  /** The detector's recorded expected range, as one rectangle over the incident. */
  detectionBand?: DetectionBand | null;
  /** A widget body: the series panel alone, filling its container. */
  compact?: boolean;
  ariaLabel?: string;
  /** Shown instead of the chart when no value is plotted. */
  emptyText?: string;
}

export function PropertyWatchSeriesChart({
  series,
  bucketSeconds,
  windowStart,
  windowEnd,
  collected,
  coverage = null,
  incidents = [],
  detectionBand = null,
  compact = false,
  ariaLabel = "Events per bucket for the selected values, and the parsed share of the scope",
  emptyText = "No value is plotted.",
}: PropertyWatchSeriesChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  const model = useMemo(
    () => buildPlot({ series, bucketSeconds, windowStart, windowEnd, collected }),
    [series, bucketSeconds, windowStart, windowEnd, collected],
  );
  const coverageModel = useMemo(
    () => (coverage ? buildCoverage(model.slots, coverage.buckets, coverage.floor) : null),
    [coverage, model.slots],
  );
  const unit = eventsUnit(bucketSeconds);
  const showCoverage = coverageModel !== null && !compact;

  useEffect(() => {
    const el = containerRef.current;
    if (!el || (model.lines.length === 0 && !showCoverage)) return;
    const reduceMotion =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const chart = echarts.init(el);
    chart.setOption(
      buildPwOption({
        model,
        palette: readPwPalette(),
        incidents,
        detectionBand,
        coverage: coverageModel,
        compact,
        reduceMotion,
      }),
    );
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [model, coverageModel, incidents, detectionBand, compact, showCoverage]);

  if (model.lines.length === 0 && !showCoverage) {
    return (
      <div className="notice" role="status">
        {emptyText}
      </div>
    );
  }

  const hasLowerBound = model.lines.some((l) => l.points.some((p) => p.kind === "LOWER_BOUND"));
  const hasGap = model.lines.some((l) =>
    l.points.some((p) => p.value === null && p.kind !== "NOT_COLLECTED"),
  );
  const summary = [
    `${unit} for ${model.lines.length} ${model.lines.length === 1 ? "value" : "values"}, in UTC.`,
    model.collectedSlots !== null
      ? `${model.collectedSlots} of ${model.windowSlots} five-minute intervals in the window were collected.`
      : "",
    "The same figures are in the values table.",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="pw-chart">
      {model.lines.length === 0 && (
        <p className="notice" role="status">
          {emptyText}
        </p>
      )}
      <div
        ref={containerRef}
        className={
          compact
            ? "pw-chart-canvas pw-chart-compact"
            : model.lines.length === 0
              ? "pw-chart-canvas pw-chart-coverage-only"
              : "pw-chart-canvas"
        }
        role="img"
        aria-label={ariaLabel}
      />
      <p className="sr-only">{summary}</p>
      <ul className="chart-legend" aria-label="Chart key">
        {model.lines.map((line, i) => (
          <li key={line.valueId}>
            <span className={`chart-key pw-key pw-key-${(i % PW_MAX_SERIES) + 1}`} aria-hidden="true" />
            {/* The value is attacker-controlled text: React renders it as text. */}
            <span className="pw-key-label">{line.label}</span>
            {line.pinned && <span className="muted">pinned</span>}
          </li>
        ))}
        {model.hasBand && (
          <li>
            <span className="chart-key chart-key-band" aria-hidden="true" />
            Expected range for the hour of the week
          </li>
        )}
        {model.uncollected.length > 0 && (
          <li>
            <span className="chart-key chart-key-gap" aria-hidden="true" />
            Not collected
          </li>
        )}
        {detectionBand && model.lines.length > 0 && (
          <li>
            <span className="chart-key chart-key-band" aria-hidden="true" />
            Expected range at detection
          </li>
        )}
        {incidents.length > 0 && (
          <li>
            <span className="chart-key chart-key-anomaly" aria-hidden="true" />
            Incident
          </li>
        )}
        {hasLowerBound && (
          <li>
            <span className="chart-key pw-key-bound" aria-hidden="true" />
            At least this many (lower bound)
          </li>
        )}
      </ul>
      <p className={compact ? "sr-only" : "subtitle chart-note"}>
        Gaps in a line are buckets that were not collected
        {hasGap ? ", values that were unknown in a lower-bound bucket, or times the value was not tracked" : ""}.
        They are not zero events; a line at 0 is a measured zero.
        {model.lines.length > 1 &&
          " The expected range is drawn for one value at a time: select a single value to see its band."}
        {model.dropped > 0 && ` ${model.dropped} more selected ${model.dropped === 1 ? "value is" : "values are"} not drawn; the chart shows at most ${PW_MAX_SERIES}.`}
        {showCoverage && " The lower panel is the share of the scope's events in which the property parsed."}
      </p>
    </div>
  );
}
