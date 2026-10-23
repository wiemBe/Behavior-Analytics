"use client";

// One table per console: one row per family and scope (HISTORY-BACKFILL §B.10.3). Numbers are
// right-aligned in tabular figures, times carry their zone, and a missing measurement is an em
// dash with the reason beside it, never a zero.

import type { HistoryConsole, HistoryScopeSource } from "@/lib/api";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime, utcTimeText } from "@/components/ui/UtcTime";

import {
  familyName,
  formatCount,
  formatDays,
  relativeAge,
} from "./historyFormat";
import { rowsOf, type HistoryRow } from "./historyRows";

import "@/components/ui/controls.css";
import "./history.css";

const MAX_GAPS_SHOWN = 3;
const MAX_SOURCES_SHOWN = 5;

function Time({ iso, now }: { iso: string | null; now: Date }) {
  if (iso === null) return <span className="muted">—</span>;
  return (
    <UtcTime
      value={iso}
      variant="compact"
      wrapZone
      age={relativeAge(iso, now)}
      ageClassName="cell-sub"
    />
  );
}

function Asked({ row }: { row: HistoryRow }) {
  if (row.asked.days === null) return <span className="muted">—</span>;
  if (row.asked.of !== null && row.asked.of !== row.asked.days) {
    return (
      <>
        {row.asked.days}
        <span className="cell-sub">of {row.asked.of} asked</span>
      </>
    );
  }
  return <>{row.asked.days}</>;
}

function Proved({ row }: { row: HistoryRow }) {
  if (row.proved.kind === "proved") {
    if (row.proved.floor === null) return <span className="muted">—</span>;
    return (
      <>
        <span className="cell-sub hb-lead">Window proved</span>
        <UtcTime value={row.proved.floor} variant="compact" wrapZone />
      </>
    );
  }
  if (row.proved.kind === "per-source") {
    return (
      <span className="muted" title="The floor is proved per source and is not in this answer">
        per source
      </span>
    );
  }
  return <span className="muted">—</span>;
}

function Read({ row }: { row: HistoryRow }) {
  const { read, state } = row;
  if (read.unreadable) {
    return <span className="muted">progress could not be read</span>;
  }
  if (read.days === null && read.buckets === null) return <span className="muted">—</span>;
  const days = read.days === null ? null : formatDays(read.days);
  const of = state.label === "Reading" && read.target !== null ? ` of ${read.target}` : "";
  return (
    <>
      {days === null ? (
        <span className="muted">—</span>
      ) : (
        <>
          {days}
          {of} days
        </>
      )}
      {read.buckets !== null && (
        <span className="cell-sub">
          {formatCount(read.buckets)} {read.buckets === 1 ? "bucket" : "buckets"}
        </span>
      )}
    </>
  );
}

function SourceShortfall({
  label,
  sources,
  count,
}: {
  label: string;
  sources: HistoryScopeSource[] | null;
  count: number | null;
}) {
  if (count === null || count <= 0) return null;
  const shown = (sources ?? []).slice(0, Math.min(MAX_SOURCES_SHOWN, count));
  const more = count - shown.length;
  const names = shown.map((source) => source.name ?? `source ${source.key}`).join(", ");
  const remainder = more > 0 ? `${shown.length > 0 ? " and " : ""}${more} more` : "";
  return (
    <div>
      <span className="muted">{label}: </span>
      {names}
      {remainder}.
    </div>
  );
}

function Reason({ row }: { row: HistoryRow }) {
  const shown = row.gaps.slice(0, MAX_GAPS_SHOWN);
  const more = row.gaps.length - shown.length;
  const hasLastError = row.lastErrorClass !== null;
  const hasCutSources = row.sourcesCutCount !== null && row.sourcesCutCount > 0;
  const hasSilentSources = row.sourcesSilentCount !== null && row.sourcesSilentCount > 0;
  if (
    row.reason === null &&
    shown.length === 0 &&
    !hasLastError &&
    !hasCutSources &&
    !hasSilentSources
  ) {
    return <span className="muted">—</span>;
  }
  return (
    <>
      {row.reason}
      {shown.length > 0 && (
        <ul className="hb-gaps" aria-label="Gaps in what was read">
          {shown.map((gap) => (
            <li key={`${gap.start}-${gap.end}`}>
              <span title={`${utcTimeText(gap.start)} to ${utcTimeText(gap.end)}`}>
                <UtcTime value={gap.start} variant="compact" />
                {" to "}
                <UtcTime value={gap.end} variant="compact" />
              </span>
              <span className="cell-sub">{gap.reason}</span>
            </li>
          ))}
          {more > 0 && <li className="cell-sub">and {more} more</li>}
        </ul>
      )}
      {hasLastError && (
        <span className="cell-sub hb-last-error warn">
          Last step failed at <UtcTime value={row.lastErrorAt} variant="compact" />{" "}
          ({row.lastErrorClass}). The next collection retries.
        </span>
      )}
      <SourceShortfall
        label="QRadar holds less than asked for"
        sources={row.sourcesCut}
        count={row.sourcesCutCount}
      />
      <SourceShortfall
        label="No event in the window"
        sources={row.sourcesSilent}
        count={row.sourcesSilentCount}
      />
    </>
  );
}

function StateCell({ row }: { row: HistoryRow }) {
  return (
    <span className={`pill pill-quiet ${row.state.tone === "neutral" ? "" : row.state.tone}`}>
      {row.state.label}
    </span>
  );
}

export function HistoryTable({ console, now }: { console: HistoryConsole; now: Date }) {
  const rows = rowsOf(console);
  return (
    <TableScroll label={`History backfill by family and scope, ${console.name}`}>
      <table className="table-dense hb-table">
        <caption className="sr-only">
          What has been read from QRadar&apos;s past, by family and scope, for {console.name}.
          Times are UTC.
        </caption>
        <thead>
          <tr>
            <th scope="col">Family</th>
            <th scope="col">Scope</th>
            <th scope="col">State</th>
            <th scope="col" className="num">
              Window asked
              <span className="cell-sub">days</span>
            </th>
            <th scope="col">
              Window proved
              <span className="cell-sub">floor, UTC</span>
            </th>
            <th scope="col" className="num">
              Read
            </th>
            <th scope="col" className="num">
              Searches
            </th>
            <th scope="col" className="num">
              Read through
              <span className="cell-sub">UTC</span>
            </th>
          </tr>
        </thead>
        {rows.map((row) => (
          <tbody key={row.key}>
            <tr className="hb-metrics">
              <th scope="row" className="hb-family">
                {familyName(row.family)}
              </th>
              <td className="cell-name">
                {row.scope}
                {row.scopeSub && <span className="cell-sub">{row.scopeSub}</span>}
              </td>
              <td>
                <StateCell row={row} />
              </td>
              <td className="num">
                <Asked row={row} />
              </td>
              <td>
                <Proved row={row} />
              </td>
              <td className="num hb-read">
                <Read row={row} />
              </td>
              <td className="num">
                {row.searches === null ? (
                  <span className="muted">—</span>
                ) : (
                  formatCount(row.searches)
                )}
              </td>
              <td className="num hb-when">
                {row.last === null ? (
                  <span className="muted">—</span>
                ) : (
                  <>
                    {row.last.through === null ? (
                      <span className="muted">—</span>
                    ) : (
                      <Time iso={row.last.through} now={now} />
                    )}
                    {row.last.finished !== null && (
                      <span className="cell-sub">
                        finished <UtcTime value={row.last.finished} variant="compact" wrapZone />
                      </span>
                    )}
                  </>
                )}
              </td>
            </tr>
            <tr className="hb-detail">
              <td colSpan={8}>
                <dl className="hb-detail-list">
                  <div>
                    <dt>Reason</dt>
                    <dd>
                      <Reason row={row} />
                    </dd>
                  </div>
                  <div>
                    <dt>Later Yes</dt>
                    <dd>
                      {row.laterYes === null ? <span className="muted">—</span> : row.laterYes}
                    </dd>
                  </div>
                </dl>
              </td>
            </tr>
          </tbody>
        ))}
      </table>
    </TableScroll>
  );
}
