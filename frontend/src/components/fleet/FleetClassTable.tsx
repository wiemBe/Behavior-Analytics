// One row per class of log source: how much it sent in the window, what that
// should have been, and how much of the window was actually measured.
//
// Every figure that can be missing is written so that missing reads as missing:
// "Not measured" for a class with no complete bucket, an em dash and its reason
// for an expectation with no baseline. Completeness sits in the row beside the
// figures it qualifies, not in a footnote.

import Link from "next/link";

import "@/components/ui/controls.css";

import { HISTORY_TEXT } from "@/components/behavior/historyOrigin";
import { TableScroll } from "@/components/ui/TableScroll";
import type { FleetClassSummary } from "@/lib/api";
import {
  classLabel,
  completenessInfo,
  expectedInfo,
  formatEpsValue,
  formatEventCount,
  hasSources,
  NOT_MEASURED_TEXT,
  openIncidentList,
} from "@/lib/fleet";

function Figure({ value, unit, why }: { value: string | null; unit?: string; why: string | null }) {
  if (value === null) {
    return (
      <>
        <span className="muted">{NOT_MEASURED_TEXT}</span>
        {why && <span className="cell-sub">{why}</span>}
      </>
    );
  }
  return (
    <>
      {value}
      {unit && <span className="unit"> {unit}</span>}
    </>
  );
}

function ClassRows({ c }: { c: FleetClassSummary }) {
  const completeness = completenessInfo(c.buckets);
  const expected = expectedInfo(c);
  const events = formatEventCount(c.events);
  const mean = formatEpsValue(c.mean_eps);
  const incidents = openIncidentList(c);
  const noComplete =
    completeness.state === "none" ? completeness.text : "No complete bucket in the window.";
  const hasTop = c.top_sources.length > 0;

  return (
    <>
      <tr className={hasTop ? "fleet-main" : undefined}>
        <th scope="row" className="cell-class">
          <Link href={`/fleet/sources?class=${c.source_class}`}>{classLabel(c.source_class)}</Link>
          <span className="cell-sub">
            {hasSources(c)
              ? `${c.sources.enabled} enabled, ${c.sources.disabled} disabled`
              : "No sources"}
          </span>
        </th>
        <td className="num">
          <Figure value={events} why={noComplete} />
          {events !== null && completeness.state === "incomplete" && (
            <span className="cell-sub">complete buckets only</span>
          )}
        </td>
        <td className="num">
          <Figure value={mean} unit="EPS" why={null} />
        </td>
        <td className="num">
          {expected.value === null ? (
            <>
              <span className="muted" aria-label="No expectation">
                —
              </span>
              <span className="cell-sub">{expected.note}</span>
            </>
          ) : (
            <>
              {expected.value}
              <span className="unit"> EPS</span>
              <span className="cell-sub">{expected.note}</span>
              {expected.partial && <span className="cell-sub">Covers part of the class only.</span>}
            </>
          )}
        </td>
        <td className="cell-completeness">
          {completeness.state === "incomplete" && (
            <span className="pill pill-quiet warn">Incomplete</span>
          )}
          <span className={completeness.state === "none" ? "muted" : undefined}>
            {completeness.text}
          </span>
          {completeness.detail && <span className="cell-sub">{completeness.detail}</span>}
          {c.buckets.complete_from_history > 0 && (
            <span className="cell-sub">
              {c.buckets.complete_from_history.toLocaleString("en-US")} of{" "}
              {c.buckets.complete.toLocaleString("en-US")} complete buckets were {HISTORY_TEXT}.
            </span>
          )}
        </td>
        <td>
          {incidents.length === 0 ? (
            <span className="muted">None open</span>
          ) : (
            <ul className="plain-list">
              {incidents.map(([label, n]) => (
                <li key={label}>
                  {label} <span className="num">{n}</span>
                </li>
              ))}
            </ul>
          )}
        </td>
      </tr>
      {hasTop && (
        <tr className="fleet-top">
          <td colSpan={6}>
            <span className="fleet-top-label">Top sources by events</span>
            <ul className="fleet-top-list">
              {c.top_sources.map((s) => (
                <li key={s.id}>
                  <Link href={`/behavior/sources/${s.id}`}>{s.name}</Link>{" "}
                  <span className="num">{s.events.toLocaleString("en-US")}</span>
                </li>
              ))}
            </ul>
          </td>
        </tr>
      )}
    </>
  );
}

export function FleetClassTable({
  classes,
  windowName,
}: {
  classes: FleetClassSummary[];
  windowName: string;
}) {
  return (
    <TableScroll label="Log-source classes">
      <table className="fleet-table">
        <caption className="sr-only">
          Log-source classes: volume over the last {windowName} against the expected volume,
          the completeness of the window and open incidents, with the busiest sources under each class.
        </caption>
        <thead>
          <tr>
            <th scope="col">Class</th>
            <th scope="col" className="num">
              Events
            </th>
            <th scope="col" className="num">
              Mean EPS
            </th>
            <th scope="col" className="num">
              Expected EPS
            </th>
            <th scope="col">Window completeness</th>
            <th scope="col">Open incidents</th>
          </tr>
        </thead>
        <tbody>
          {classes.map((c) => (
            <ClassRows key={c.source_class} c={c} />
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
