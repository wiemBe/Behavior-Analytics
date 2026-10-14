// The honesty line for every measured figure on the screen: how much of the
// requested span was actually collected. One banner, not a card; its state is
// named in words. For incomplete input the API's reason is the sentence: it
// already carries the coverage counts, so repeating the local breakdown would
// state the same fact twice.

import type { RuleStatisticsWindow } from "@/lib/api";
import {
  daysLabel,
  formatSpan,
  windowBreakdown,
  windowState,
} from "./ruleStatistics";

export function MeasurementWindow({ window: w }: { window: RuleStatisticsWindow }) {
  const state = windowState(w);
  const requested = `${w.days} ${daysLabel(w.days)}`;

  return (
    <p
      className={`stats-window ${state === "complete" ? "ok" : state === "partial" ? "warn" : "neutral"}`}
      role="region"
      aria-label="Measurement window"
    >
      {state === "complete" ? (
        <>
          <strong>Complete.</strong> Matches measured over {formatSpan(w.measured_seconds)} of the
          last {requested}: {windowBreakdown(w)}.
        </>
      ) : (
        <>
          <strong>{state === "partial" ? "PARTIAL." : "INSUFFICIENT_DATA."}</strong>{" "}
          {w.reason ??
            (state === "partial"
              ? "The requested span is not fully collected."
              : "No complete rule-match window exists in the requested span.")}
        </>
      )}{" "}
      Times are UTC.
    </p>
  );
}
