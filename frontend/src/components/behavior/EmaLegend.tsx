"use client";

// The text pieces that go with the EMA lines of any chart: the legend entries, the screen-reader
// summary and the note that the lines are display only. They read `behavior.chart.ema.*`, so no
// chart copies the strings into its own catalog.

import { useTranslations } from "next-intl";

import { spanText, type EmaPeriod } from "@/lib/ema";
import { emaDrawn, type EmaLine } from "@/lib/emaSeries";

/**
 * One legend entry per enabled average, inside the chart's `<ul class="chart-legend">`. `of`
 * names the line they average when the chart draws several; an entry for a period that never had
 * a run says so rather than vanishing.
 */
export function EmaLegendItems({
  lines,
  bucketSeconds,
  of,
}: {
  lines: ReadonlyArray<EmaLine>;
  bucketSeconds: number | null;
  of?: string;
}) {
  const t = useTranslations("behavior.chart");
  return (
    <>
      {lines.map((line) => {
        const span = spanText(line.period, bucketSeconds);
        return (
          <li key={`ema-${line.period}`}>
            <span
              className={`chart-key chart-key-ema chart-key-ema-${line.period}`}
              aria-hidden="true"
            />
            {of == null
              ? t("ema.key", { period: line.period, span })
              : t("ema.keyOf", { period: line.period, span, line: of })}
            {!emaDrawn(line) && <span> — {t("ema.notEnough")}</span>}
          </li>
        );
      })}
    </>
  );
}

/** The averages as prose, for a reader who cannot see the canvas. Nothing while none is on. */
export function EmaSummary({ periods, of }: { periods: ReadonlyArray<EmaPeriod>; of?: string }) {
  const t = useTranslations("behavior.chart");
  if (periods.length === 0) return null;
  const list = periods.map((p) => spanText(p, null)).join(", ");
  return (
    <p className="sr-only">
      {of == null ? t("ema.summary", { periods: list }) : t("ema.summaryOf", { periods: list, line: of })}
    </p>
  );
}

/** The note that the lines are drawn in the browser and take no part in detection. */
export function EmaNote({ periods }: { periods: ReadonlyArray<EmaPeriod> }) {
  const t = useTranslations("behavior.chart");
  if (periods.length === 0) return null;
  return <p className="subtitle chart-note">{t("ema.note")}</p>;
}
