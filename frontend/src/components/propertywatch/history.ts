import type { PwWatchHistory } from "@/lib/api";
import { utcTimeText } from "@/components/ui/UtcTime";

import { pwSentence } from "./pwReason";
import { ENGLISH_PW, type PwI18n } from "./pwText";

const days = (value: number | null, i18n: PwI18n) =>
  value === null
    ? i18n.t("propertyWatch.history.unknownDays")
    : value.toLocaleString(i18n.locale === "tr" ? "tr-TR" : "en-US", {
        maximumFractionDigits: 1,
      });

export function historyProgress(history: PwWatchHistory, i18n: PwI18n = ENGLISH_PW): string {
  return i18n.t("propertyWatch.history.progress", {
    read: days(history.days_read, i18n),
    requested: history.days_requested ?? i18n.t("propertyWatch.history.requestedDays"),
  });
}

/** The one line beside the status pill while the past is being read. */
export function historyReadingReason(history: PwWatchHistory, i18n: PwI18n = ENGLISH_PW): string {
  return i18n.t("propertyWatch.history.readingReason", {
    progress: historyProgress(history, i18n),
  });
}

export function historyHeaderText(history: PwWatchHistory, i18n: PwI18n = ENGLISH_PW): string {
  const progress = historyProgress(history, i18n);
  switch (history.state) {
    case "NOT_REQUESTED":
      return i18n.t("propertyWatch.history.header.NOT_REQUESTED");
    case "READING":
      return i18n.t("propertyWatch.history.header.READING", { progress });
    case "COMPLETE":
      return i18n.t("propertyWatch.history.header.COMPLETE", { progress });
    case "PARTIAL":
      return i18n.t("propertyWatch.history.header.PARTIAL", { progress });
    case "UNAVAILABLE":
      return i18n.t("propertyWatch.history.header.UNAVAILABLE");
  }
}

export function historyStateText(history: PwWatchHistory, i18n: PwI18n = ENGLISH_PW): string | null {
  const requested = history.days_requested ?? i18n.t("propertyWatch.history.requestedDays");
  const progress = historyProgress(history, i18n);
  switch (history.state) {
    case "NOT_REQUESTED":
      return null;
    case "READING":
      return i18n.t("propertyWatch.history.state.READING", { progress });
    case "COMPLETE": {
      const finished =
        history.finished_at?.slice(0, 10) ?? i18n.t("propertyWatch.history.state.unrecordedDate");
      return i18n.t("propertyWatch.history.state.COMPLETE", {
        days: days(history.days_read, i18n),
        finished,
      });
    }
    case "PARTIAL":
      if (history.floor) {
        return i18n.t("propertyWatch.history.state.PARTIAL_FLOOR", {
          progress,
          floor: utcTimeText(history.floor),
        });
      }
      return i18n.t("propertyWatch.history.state.PARTIAL", {
        progress,
        reason: history.reason
          ? pwSentence(
              history.reason,
              { code: history.reason_code, params: history.reason_params },
              i18n,
            )
          : i18n.t("propertyWatch.history.state.PARTIAL_FALLBACK"),
      });
    case "UNAVAILABLE":
      return i18n.t("propertyWatch.history.state.UNAVAILABLE", { requested });
  }
}

