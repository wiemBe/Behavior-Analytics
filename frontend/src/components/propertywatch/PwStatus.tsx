// A watch's status as a pill with its reason beside it, as `/custom-rules` shows a rule's.
// The status is always a word, so colour is never the only carrier; a watch that has never run
// says so and is not drawn as healthy.

import type { PwWatchStatus } from "@/lib/api";

import "./propertywatch.css";
import { statusLabel, statusReason, statusTone } from "./pwFormat";

export function PwStatus({
  status,
  reason,
  strong = false,
  inline = false,
}: {
  status: PwWatchStatus | null;
  reason: string | null;
  strong?: boolean;
  /** Pill and reason on one wrapping line, for a page header. */
  inline?: boolean;
}) {
  return (
    <div className={inline ? "pw-status pw-status-inline" : "pw-status"}>
      <span className={`pill${strong ? " pill-strong" : ""} ${statusTone(status)}`}>
        {statusLabel(status)}
      </span>
      <span className="pw-status-reason">{statusReason(status, reason)}</span>
    </div>
  );
}
