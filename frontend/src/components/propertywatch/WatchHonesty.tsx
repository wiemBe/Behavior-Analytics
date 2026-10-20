// The honesty line of a watch page: what is not known about the window being shown, said once
// where it changes how the chart and the table are read (design §G.13.3, item 5).
//
// Every note is derived from fields the API sent; none is a verdict. A note that would be
// empty is not drawn, and a collection fault is never worded as a finding about the events.

import type { PropertyWatch, PropertyWatchCatalog, PwBucket } from "@/lib/api";
import { formatDateTime } from "@/lib/health";

import { statusLabel, statusReason } from "./pwFormat";
import { historyGapText, historyStateText } from "./history";

export interface HonestyNote {
  /** The word that names the condition, so colour is never the only carrier. */
  label: string;
  text: string;
  tone: "warn" | "crit" | "";
}

const FAULT_STATUSES = new Set([
  "NOT_APPLICABLE",
  "SCOPE_STALE",
  "SCOPE_TOO_LARGE",
  "SCOPE_EMPTY",
  "STALE",
]);

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** The notes for a window of `hours`, from the watch, the switches and the stored buckets. */
export function honestyNotes(input: {
  watch: Pick<PropertyWatch, "status" | "status_reason" | "history">;
  catalog: Pick<PropertyWatchCatalog, "collection_enabled"> | null;
  buckets: PwBucket[] | null;
  hours: number;
}): HonestyNote[] {
  const { watch, catalog, buckets, hours } = input;
  const notes: HonestyNote[] = [];

  const historyText = historyStateText(watch.history);
  if (historyText) {
    notes.push({
      label: watch.history.state === "READING" ? "READING HISTORY" : `HISTORY ${watch.history.state}`,
      tone: watch.history.state === "COMPLETE" ? "" : "warn",
      text: historyText,
    });
  }
  for (const gap of watch.history.gaps ?? []) {
    notes.push({ label: "HISTORY GAP", tone: "warn", text: historyGapText(gap) });
  }
  if (watch.history.progress_unreadable) {
    notes.push({
      label: "HISTORY PROGRESS UNREADABLE",
      tone: "warn",
      text: "Stored history progress could not be read, so no progress figure is shown. This is not zero days read.",
    });
  }

  if (catalog && !catalog.collection_enabled) {
    notes.push({
      label: "COLLECTION OFF",
      tone: "warn",
      text: "PW_COLLECTION_ENABLED is false, so no search is sent for any watch. The buckets below are what was collected before it was switched off.",
    });
  }
  if (watch.status === null) {
    notes.push({ label: "NEVER RUN", tone: "", text: statusReason(null, watch.status_reason) });
  } else if (FAULT_STATUSES.has(watch.status)) {
    notes.push({
      label: statusLabel(watch.status).toUpperCase(),
      tone: "warn",
      text: statusReason(watch.status, watch.status_reason),
    });
  }

  if (buckets === null) {
    notes.push({
      label: "NOT LOADED",
      tone: "crit",
      text: "The stored buckets could not be loaded, so collection health for this window is not shown. Reload the page to try again.",
    });
    return notes;
  }

  const width = buckets[0]?.bucket_seconds ?? 300;
  const intervals = Math.floor((hours * 3600) / width);
  const collected = buckets.length;
  if (collected === 0) {
    notes.push({
      label: "NOT COLLECTED",
      tone: "warn",
      text: `No bucket was collected in the last ${hours} h. The chart is empty because nothing was measured, not because nothing happened.`,
    });
    return notes;
  }
  // Intervals, not rows: an interval with no stored bucket is uncollected (guidelines §4).
  if (collected < intervals) {
    const missing = intervals - collected;
    notes.push({
      label: "NOT COLLECTED",
      tone: "warn",
      text: `${missing.toLocaleString("en-US")} of ${intervals.toLocaleString("en-US")} five-minute ${plural(missing, "interval", "intervals")} in this window ${plural(missing, "was", "were")} not collected. They are gaps, not zero events.`,
    });
  }

  const lower = buckets.filter((b) => b.values_measurement === "LOWER_BOUND");
  if (lower.length > 0) {
    notes.push({
      label: "TRUNCATED",
      tone: "warn",
      text: `${lower.length.toLocaleString("en-US")} of ${collected.toLocaleString("en-US")} collected ${plural(collected, "bucket", "buckets")} in this window ${plural(lower.length, "is a lower bound", "are lower bounds")}; absent values in ${plural(lower.length, "it are", "them are")} unknown, not zero.`,
    });
  }
  const partial = buckets.filter((b) => !b.totals_complete);
  if (partial.length > 0) {
    notes.push({
      label: "PARTIAL",
      tone: "warn",
      text: `${partial.length.toLocaleString("en-US")} of ${collected.toLocaleString("en-US")} collected ${plural(collected, "bucket", "buckets")} had no complete count of the scope's events, so their parsed share is not known.`,
    });
  }

  const refused = [...buckets].reverse().find((b) => b.rejected_over_quota > 0);
  if (refused) {
    notes.push({
      label: "ADMISSION",
      tone: "",
      text: `${refused.rejected_over_quota.toLocaleString("en-US")} ${plural(refused.rejected_over_quota, "value was", "values were")} refused admission at ${formatDateTime(refused.bucket_start)}: the watch already tracks its maximum. They are counted in the bucket, not plotted.`,
    });
  }

  const changedAt = buckets.findIndex((b) => b.scope_changed);
  if (changedAt >= 0) {
    const now = buckets[changedAt];
    const before = changedAt > 0 ? buckets[changedAt - 1].scope_member_count : null;
    const counts =
      before !== null && now.scope_member_count !== null
        ? `: ${before} to ${now.scope_member_count} sources`
        : now.scope_member_count !== null
          ? `: now ${now.scope_member_count} sources`
          : "";
    notes.push({
      label: "MEMBERSHIP CHANGED",
      tone: "",
      text: `The scope's membership changed at ${formatDateTime(now.bucket_start)}${counts}. Buckets inside the grace window after a change are insufficient data, not normal.`,
    });
  }
  return notes;
}

export function WatchHonesty({ notes }: { notes: HonestyNote[] }) {
  if (notes.length === 0) return null;
  return (
    <div className="pw-honesty" role="group" aria-label="What is not known about this window">
      {notes.map((n) => (
        <p key={n.label + n.text} className={`banner${n.tone ? ` ${n.tone}` : ""}`}>
          <span>
            <strong>{n.label}.</strong> {n.text}
          </span>
        </p>
      ))}
    </div>
  );
}
