// A watch's status as a pill with its reason beside it, as `/custom-rules` shows a rule's.
// The status is always a word, so colour is never the only carrier; a watch that has never run
// says so and is not drawn as healthy.

import type { PwWatchHistory, PwWatchStatus } from "@/lib/api";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import "./propertywatch.css";
import { statusLabel, statusTone } from "./pwFormat";
import { codedStatusReason } from "./pwReason";
import { historyReadingReason } from "./history";

export function PwStatus({
  status,
  reason,
  reasonCode = null,
  reasonParams = null,
  strong = false,
  inline = false,
  history = null,
}: {
  status: PwWatchStatus | null;
  reason: string | null;
  /** The code of `reason` and its raw values (absent on a status stored before codes). */
  reasonCode?: string | null;
  reasonParams?: Record<string, unknown> | null;
  strong?: boolean;
  /** Pill and reason on one wrapping line, for a page header. */
  inline?: boolean;
  history?: PwWatchHistory | null;
}) {
  const i18n = useBehaviorI18n();
  const shownReason =
    status === "READING_HISTORY" && history
      ? historyReadingReason(history, i18n)
      : codedStatusReason(status, reason, i18n, { code: reasonCode, params: reasonParams });
  return (
    <div className={inline ? "pw-status pw-status-inline" : "pw-status"}>
      <span className={`pill${strong ? " pill-strong" : ""} ${statusTone(status)}`}>
        {statusLabel(status, i18n)}
      </span>
      <span className="pw-status-reason">{shownReason}</span>
    </div>
  );
}
