// The chart key's entry for the history span, and the assistive-technology statement beneath a
// chart. Both say the same words (`historyOrigin.ts`); the key is drawn only when the chart drew
// a span, so a series without a history bucket keeps today's key.

import "./history.css";

import type { HistoryOrigin } from "@/lib/api";

import {
  HISTORY_TEXT_CAPITAL,
  historySummary,
  originMarker,
  type OriginRow,
} from "./historyOrigin";

export function HistoryKey() {
  return (
    <li>
      <span className="chart-key chart-key-history" aria-hidden="true" />
      {HISTORY_TEXT_CAPITAL}
    </li>
  );
}

/** Nothing when no bucket is history or unknown. */
export function HistoryText({ rows }: { rows: OriginRow[] }) {
  const text = historySummary(rows);
  return text ? <p className="sr-only">{text}</p> : null;
}

/** The text marker of a table row: nothing for a live bucket or a response with no origin. */
export function OriginMarker({ origin }: { origin: HistoryOrigin | null | undefined }) {
  const text = originMarker(origin);
  return text ? <span className="history-marker">{text}</span> : null;
}
