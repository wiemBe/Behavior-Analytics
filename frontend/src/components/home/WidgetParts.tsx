"use client";

// The parts the Home tables put inside a `WidgetFrame`: the qualifier list, the
// unmeasured dash and the time cell. The frame and its states are in `WidgetFrame`.

import { NOT_MEASURED } from "@/lib/behavior";
import { formatDateTime, formatDateTimeCompact } from "@/lib/health";
import { formatAgo } from "@/lib/home/format";

import "./tables.css";

/** An unmeasured figure: the dash, with the reason for assistive technology. */
export function Unmeasured({ reason }: { reason?: string | null }) {
  return (
    <span className="muted" title={reason ?? undefined}>
      {NOT_MEASURED}
      {reason && <span className="sr-only"> {reason}</span>}
    </span>
  );
}

/**
 * An instant in UTC with its zone, as a `<time>`, and its age in muted text.
 * Null prints a dash, not a date.
 */
export function When({ iso }: { iso: string | null }) {
  if (!iso) return <Unmeasured reason="No time recorded" />;
  const ago = formatAgo(iso, Date.now());
  return (
    <span className="tw-when">
      <time dateTime={iso} title={formatDateTime(iso)}>
        {formatDateTimeCompact(iso)}
      </time>
      {ago && <span className="tw-sub">{ago}</span>}
    </span>
  );
}
