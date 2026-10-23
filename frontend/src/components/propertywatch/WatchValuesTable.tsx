import Link from "next/link";
// The values table of a watch page: every figure the chart carries, as text, so assistive
// technology and a reader who cannot see the canvas lose nothing (guidelines §10).
//
// Value text is attacker-controlled (a username from a log line). React renders every cell as
// text; nothing here is markup. Numbers are right-aligned with tabular figures and carry their
// unit in the column header. A reading that is not a number says what it is in words.
//
// The baseline state and the open incidents sit under the value, not in a column of their own:
// at 1024 px a seventh column was clipped, and they describe the value, not a measurement.
// A long value takes two lines; the full text is behind a disclosure, which a keyboard reaches.

import type { AnomalySummary, PwBucket, PwValue, PwWatchHistory } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { UtcTime, utcTimeText } from "@/components/ui/UtcTime";

import {
  baselineText,
  eventsUnit,
  expectedRangeText,
  formatShare,
  pwTypeLabel,
  readingMeaning,
  readingText,
  shareOfParsed,
} from "./pwFormat";

/** A value longer than this is cut to two lines in the cell and offered in full on request. */
const FULL_VALUE_FROM = 60;

export type ValueSort = "events" | "value" | "first_seen";

/** The value as an analyst reads it: a combination's components joined, else its text. */
export function valueText(v: Pick<PwValue, "value_key" | "components">): string {
  return v.components.length > 1 ? v.components.join(" × ") : v.value_key;
}

export const ACTIVE_STATES = new Set(["CANDIDATE", "OPEN", "RECOVERING"]);

/** Active incidents by the value they are about. Keyed by the value's text, as the list is. */
export function activeIncidentsByValue(items: AnomalySummary[]): Map<string, AnomalySummary[]> {
  const map = new Map<string, AnomalySummary[]>();
  for (const a of items) {
    if (!a.pw_value || !ACTIVE_STATES.has(a.state)) continue;
    const list = map.get(a.pw_value.value_key) ?? [];
    list.push(a);
    map.set(a.pw_value.value_key, list);
  }
  return map;
}

const SORT_LABELS: Record<ValueSort, string> = {
  events: "Events in the latest bucket",
  value: "Value",
  first_seen: "First seen",
};

export function WatchValuesTable({
  values,
  latest,
  selected,
  maxSelected,
  onToggle,
  incidents,
  sort,
  sortHref,
  history = null,
}: {
  values: PwValue[];
  /** The newest collected bucket, for the share column. */
  latest: PwBucket | null;
  selected: ReadonlySet<string>;
  maxSelected: number;
  onToggle: (valueId: string) => void;
  incidents: Map<string, AnomalySummary[]>;
  sort: ValueSort;
  /** The link a sortable header points to. */
  sortHref: (sort: ValueSort) => string;
  history?: PwWatchHistory | null;
}) {
  const unit = eventsUnit(latest?.bucket_seconds ?? 300);
  const full = selected.size >= maxSelected;
  const header = (key: ValueSort, text: string, className?: string) => (
    <th
      scope="col"
      className={className}
      aria-sort={sort === key ? (key === "events" ? "descending" : "ascending") : "none"}
    >
      <Link className="sort-link" href={sortHref(key)}>
        {text}
      </Link>
    </th>
  );
  return (
    <table className="table-dense pw-values" aria-label="Tracked values">
      <caption className="sr-only">
        Tracked values, pinned first. The latest bucket is
        {latest ? (
          <>
            {" "}
            <UtcTime value={latest.bucket_start} />
          </>
        ) : (
          " not available"
        )}
        ; counts are {unit}.
        {history?.floor ? (
          <>
            {" "}
            First seen includes the history read from QRadar, from <UtcTime value={history.floor} />.
          </>
        ) : (
          ""
        )}
        Select up to {maxSelected} values to plot them.
      </caption>
      <thead>
        <tr>
          <th scope="col" className="pw-col-plot">
            <span className="sr-only">Plot</span>
          </th>
          {header("value", SORT_LABELS.value)}
          {header("events", "Events, latest bucket", "num")}
          <th scope="col" className="num">
            Expected range
          </th>
          <th scope="col" className="num">
            Share of parsed events
          </th>
          {header(
            "first_seen",
            history?.floor
              ? `First seen (UTC; history read from ${utcTimeText(history.floor)})`
              : "First seen (UTC)",
          )}
        </tr>
      </thead>
      <tbody>
        {values.map((v) => {
          const text = valueText(v);
          const checked = selected.has(v.id);
          const open = incidents.get(v.value_key) ?? [];
          return (
            <tr key={v.id} aria-selected={checked || undefined}>
              <td className="pw-col-plot">
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!checked && full}
                  onChange={() => onToggle(v.id)}
                  aria-label={`Plot ${text}`}
                />
              </td>
              <td className="cell-name pw-value-cell">
                <span className="pw-value-text pw-value-clamp">{text}</span>
                {text.length > FULL_VALUE_FROM && (
                  <details className="pw-value-full">
                    <summary>Show the full value</summary>
                    {/* Attacker-controlled: rendered as text, never as markup. */}
                    <span className="pw-value-text">{text}</span>
                  </details>
                )}
                {v.pinned && <span className="cell-sub">pinned</span>}
                {v.truncated && <span className="cell-sub">value text was cut at the length limit</span>}
                {!v.active && <span className="cell-sub">no longer tracked</span>}
                <span className="cell-sub">{baselineText(v)}</span>
                {open.map((a) => (
                  <span key={a.id} className="cell-sub">
                    <span className={`pill pill-quiet ${stateTone(a.state)}`}>
                      {pwTypeLabel(a.anomaly_type)} · {a.state}
                    </span>
                  </span>
                ))}
              </td>
              <td className="num" title={readingMeaning(v.reading)}>
                {readingText(v.reading, v.events, v.measurement)}
              </td>
              <td className="num">{expectedRangeText(v)}</td>
              <td className="num">{formatShare(shareOfParsed(v, latest))}</td>
              <td className="num cell-when nowrap">
                {v.first_seen_at ? (
                  <UtcTime value={v.first_seen_at} variant="compact" />
                ) : (
                  "—"
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
