import type { PwWatchHistory } from "@/lib/api";
import { utcTimeText } from "@/components/ui/UtcTime";


const days = (value: number | null) =>
  value === null ? "an unknown number" : value.toLocaleString("en-US", { maximumFractionDigits: 1 });

export function historyProgress(history: PwWatchHistory): string {
  return `${days(history.days_read)} of ${history.days_requested ?? "the requested"} days`;
}

/** The one line beside the status pill while the past is being read. */
export function historyReadingReason(history: PwWatchHistory): string {
  return `${historyProgress(history)} read. Nothing is judged until it finishes.`;
}

export function historyHeaderText(history: PwWatchHistory): string {
  switch (history.state) {
    case "NOT_REQUESTED":
      return "History not requested";
    case "READING":
      return `Reading history: ${historyProgress(history)}`;
    case "COMPLETE":
      return `History complete: ${historyProgress(history)}`;
    case "PARTIAL":
      return `History partial: ${historyProgress(history)}`;
    case "UNAVAILABLE":
      return "History unavailable";
  }
}

export function historyStateText(history: PwWatchHistory): string | null {
  const requested = history.days_requested ?? "the requested";
  switch (history.state) {
    case "NOT_REQUESTED":
      return null;
    case "READING":
      return `Reading history from QRadar: ${historyProgress(history)}. Nothing is judged until it finishes.`;
    case "COMPLETE": {
      const finished = history.finished_at?.slice(0, 10) ?? "an unrecorded date";
      return `History: ${days(history.days_read)} days read from QRadar on ${finished}, used for baselines and new-value checks. Those buckets were not judged.`;
    }
    case "PARTIAL":
      if (history.floor) {
        return `History: ${historyProgress(history)}. QRadar holds events of this scope from ${utcTimeText(history.floor)}; earlier buckets are not claimed as zero.`;
      }
      return `History: ${historyProgress(history)}. ${history.reason ?? "The requested history could not be read in full."}`;
    case "UNAVAILABLE":
      return `QRadar holds no events of this scope in the last ${requested} days. The watch learns forward.`;
  }
}

