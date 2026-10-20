// A watch's status as a pill with its reason beside it, as `/custom-rules` shows a rule's.
// The status is always a word, so colour is never the only carrier; a watch that has never run
// says so and is not drawn as healthy.

import type { PwWatchHistory, PwWatchStatus } from "@/lib/api";

import "./propertywatch.css";
import { statusLabel, statusReason, statusTone } from "./pwFormat";
import { historyReadingReason } from "./history";

export function PwStatus({
  status,
  reason,
  strong = false,
  inline = false,
  history = null,
}: {
  status: PwWatchStatus | null;
  reason: string | null;
  strong?: boolean;
  /** Pill and reason on one wrapping line, for a page header. */
  inline?: boolean;
  history?: PwWatchHistory | null;
}) {
  const shownReason =
    status === "READING_HISTORY" && history ? historyReadingReason(history) : statusReason(status, reason);
  return (
    <div className={inline ? "pw-status pw-status-inline" : "pw-status"}>
      <span className={`pill${strong ? " pill-strong" : ""} ${statusTone(status)}`}>
        {statusLabel(status)}
      </span>
      <span className="pw-status-reason">{shownReason}</span>
    </div>
  );
}
