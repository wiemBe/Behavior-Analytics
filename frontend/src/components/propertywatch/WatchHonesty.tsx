// The honesty line of a watch page: what is not known about the window being shown, said once
// where it changes how the chart and the table are read (design §G.13.3, item 5).

import type { ReactNode } from "react";

import { utcTimeText } from "@/components/ui/UtcTime";
import { enumLabel } from "@/i18n/enumLabels";
import { formatCount } from "@/lib/format";
import type { PropertyWatch, PropertyWatchCatalog, PwBucket, PwWatchHistory } from "@/lib/api";

import { historyProgress, historyStateText } from "./history";
import { statusLabel, statusReason } from "./pwFormat";
import { ENGLISH_PW, type PwI18n } from "./pwText";

export interface HonestyNote {
  label: string;
  text: ReactNode;
  tone: "warn" | "crit" | "";
}

export function HistoryGapLine({
  gap,
  i18n = ENGLISH_PW,
}: {
  gap: NonNullable<PwWatchHistory["gaps"]>[number];
  i18n?: PwI18n;
}) {
  const reason = gap.reason.trim().replace(/[.:;]+$/, "");
  return (
    <>
      {i18n.t("propertyWatch.honesty.historyGap", {
        start: utcTimeText(gap.start),
        end: utcTimeText(gap.end),
        reason: reason
          ? i18n.t("propertyWatch.honesty.historyGapReason", { reason })
          : i18n.t("propertyWatch.honesty.period"),
      })}
    </>
  );
}

const FAULT_STATUSES = new Set([
  "NOT_APPLICABLE",
  "SCOPE_STALE",
  "SCOPE_TOO_LARGE",
  "SCOPE_EMPTY",
  "STALE",
]);

export function honestyNotes(input: {
  watch: Pick<PropertyWatch, "status" | "status_reason" | "history">;
  catalog: Pick<PropertyWatchCatalog, "collection_enabled"> | null;
  buckets: PwBucket[] | null;
  hours: number;
  i18n?: PwI18n;
}): HonestyNote[] {
  const { watch, catalog, buckets, hours, i18n = ENGLISH_PW } = input;
  const t = i18n.t;
  const notes: HonestyNote[] = [];
  const historyText =
    watch.history.state === "PARTIAL" && watch.history.floor ? (
      <>
        {t("propertyWatch.history.state.PARTIAL_FLOOR", {
          progress: historyProgress(watch.history, i18n),
          floor: utcTimeText(watch.history.floor),
        })}
      </>
    ) : (
      historyStateText(watch.history, i18n)
    );
  if (historyText) {
    notes.push({
      label:
        watch.history.state === "READING"
          ? t("propertyWatch.honesty.label.readingHistory")
          : t("propertyWatch.honesty.label.history", {
              state: enumLabel(i18n.t, "propertyWatchHistoryState", watch.history.state),
            }),
      tone: watch.history.state === "COMPLETE" ? "" : "warn",
      text: historyText,
    });
  }
  for (const gap of watch.history.gaps ?? []) {
    notes.push({
      label: t("propertyWatch.honesty.label.historyGap"),
      tone: "warn",
      text: <HistoryGapLine gap={gap} i18n={i18n} />,
    });
  }
  if (watch.history.progress_unreadable) {
    notes.push({
      label: t("propertyWatch.honesty.label.historyUnreadable"),
      tone: "warn",
      text: t("propertyWatch.honesty.historyUnreadable"),
    });
  }
  if (catalog && !catalog.collection_enabled) {
    notes.push({
      label: t("propertyWatch.honesty.label.collectionOff"),
      tone: "warn",
      text: t("propertyWatch.honesty.collectionOff"),
    });
  }
  if (watch.status === null) {
    notes.push({
      label: t("propertyWatch.honesty.label.neverRun"),
      tone: "",
      text: statusReason(null, watch.status_reason, i18n),
    });
  } else if (FAULT_STATUSES.has(watch.status)) {
    notes.push({
      label: statusLabel(watch.status, i18n).toLocaleUpperCase(i18n.locale),
      tone: "warn",
      text: statusReason(watch.status, watch.status_reason, i18n),
    });
  }
  if (buckets === null) {
    notes.push({
      label: t("propertyWatch.honesty.label.notLoaded"),
      tone: "crit",
      text: t("propertyWatch.honesty.notLoaded"),
    });
    return notes;
  }
  const width = buckets[0]?.bucket_seconds ?? 300;
  const intervals = Math.floor((hours * 3600) / width);
  const collected = buckets.length;
  if (collected === 0) {
    notes.push({
      label: t("propertyWatch.honesty.label.notCollected"),
      tone: "warn",
      text: t("propertyWatch.honesty.noneCollected", {
        hours: formatCount(hours, i18n.locale),
      }),
    });
    return notes;
  }
  if (collected < intervals) {
    const missing = intervals - collected;
    notes.push({
      label: t("propertyWatch.honesty.label.notCollected"),
      tone: "warn",
      text: t("propertyWatch.honesty.missing", {
        missing: formatCount(missing, i18n.locale),
        total: formatCount(intervals, i18n.locale),
        intervals: t(missing === 1 ? "propertyWatch.format.intervalOne" : "propertyWatch.format.intervalMany"),
        verb: t(missing === 1 ? "propertyWatch.honesty.was" : "propertyWatch.honesty.were"),
      }),
    });
  }
  const lower = buckets.filter((bucket) => bucket.values_measurement === "LOWER_BOUND");
  if (lower.length > 0) {
    notes.push({
      label: t("propertyWatch.honesty.label.truncated"),
      tone: "warn",
      text: t("propertyWatch.honesty.lower", {
        lower: formatCount(lower.length, i18n.locale),
        collected: formatCount(collected, i18n.locale),
        buckets: t(collected === 1 ? "propertyWatch.format.bucketOne" : "propertyWatch.format.bucketMany"),
        verb: t(
          lower.length === 1 ? "propertyWatch.honesty.isLower" : "propertyWatch.honesty.areLower",
        ),
        pronoun: t(lower.length === 1 ? "propertyWatch.honesty.it" : "propertyWatch.honesty.them"),
      }),
    });
  }
  const partial = buckets.filter((bucket) => !bucket.totals_complete);
  if (partial.length > 0) {
    notes.push({
      label: t("propertyWatch.honesty.label.partial"),
      tone: "warn",
      text: t("propertyWatch.honesty.partial", {
        partial: formatCount(partial.length, i18n.locale),
        collected: formatCount(collected, i18n.locale),
        buckets: t(collected === 1 ? "propertyWatch.format.bucketOne" : "propertyWatch.format.bucketMany"),
      }),
    });
  }
  const refused = [...buckets].reverse().find((bucket) => bucket.rejected_over_quota > 0);
  if (refused) {
    notes.push({
      label: t("propertyWatch.honesty.label.admission"),
      tone: "",
      text: t("propertyWatch.honesty.admission", {
        count: formatCount(refused.rejected_over_quota, i18n.locale),
        values: t(
          refused.rejected_over_quota === 1
            ? "propertyWatch.honesty.valueWas"
            : "propertyWatch.honesty.valuesWere",
        ),
        time: utcTimeText(refused.bucket_start),
      }),
    });
  }
  const changedAt = buckets.findIndex((bucket) => bucket.scope_changed);
  if (changedAt >= 0) {
    const now = buckets[changedAt];
    const before = changedAt > 0 ? buckets[changedAt - 1].scope_member_count : null;
    const counts =
      before !== null && now.scope_member_count !== null
        ? t("propertyWatch.honesty.membershipCounts", {
            before: formatCount(before, i18n.locale),
            after: formatCount(now.scope_member_count, i18n.locale),
          })
        : now.scope_member_count !== null
          ? t("propertyWatch.honesty.membershipNow", {
              count: formatCount(now.scope_member_count, i18n.locale),
            })
          : "";
    notes.push({
      label: t("propertyWatch.honesty.label.membership"),
      tone: "",
      text: t("propertyWatch.honesty.membership", {
        time: utcTimeText(now.bucket_start),
        counts,
      }),
    });
  }
  return notes;
}

export function WatchHonesty({
  notes,
  i18n = ENGLISH_PW,
}: {
  notes: HonestyNote[];
  i18n?: PwI18n;
}) {
  if (notes.length === 0) return null;
  return (
    <div className="pw-honesty" role="group" aria-label={i18n.t("propertyWatch.honesty.groupLabel")}>
      {notes.map((note, index) => (
        <p key={`${note.label}-${index}`} className={`banner${note.tone ? ` ${note.tone}` : ""}`}>
          <span>
            <strong>{note.label}.</strong> {note.text}
          </span>
        </p>
      ))}
    </div>
  );
}
