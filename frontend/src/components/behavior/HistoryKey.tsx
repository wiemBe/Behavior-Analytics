// The chart key's entry for the history span, and the assistive-technology statement beneath a
// chart. Both say the same words (`historyOrigin.ts`); the key is drawn only when the chart drew
// a span, so a series without a history bucket keeps today's key.

import "./history.css";

import { useTranslations } from "next-intl";

import type { HistoryOrigin } from "@/lib/api";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { historySummary, originMarker, type HistoryWords, type OriginRow } from "./historyOrigin";

/** The history words of the interface language, for the functions of `historyOrigin`. */
export function useHistoryWords(): HistoryWords {
  const { t, locale } = useBehaviorI18n();
  return { say: t, locale };
}

export function HistoryKey() {
  const t = useTranslations("behavior.history");
  return (
    <li>
      <span className="chart-key chart-key-history" aria-hidden="true" />
      {t("textCapital")}
    </li>
  );
}

/** Nothing when no bucket is history or unknown. */
export function HistoryText({ rows }: { rows: OriginRow[] }) {
  const words = useHistoryWords();
  const text = historySummary(rows, words);
  return text ? <p className="sr-only">{text}</p> : null;
}

/** The text marker of a table row: nothing for a live bucket or a response with no origin. */
export function OriginMarker({ origin }: { origin: HistoryOrigin | null | undefined }) {
  const words = useHistoryWords();
  const text = originMarker(origin, words);
  return text ? <span className="history-marker">{text}</span> : null;
}
