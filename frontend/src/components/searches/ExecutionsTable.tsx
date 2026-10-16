// Execution history of one scheduled search.
//
// A truncated run was cut off at the search's row cap. Its count is therefore
// a lower bound ("≥ 500"), and it cannot say whether the threshold was met, so
// the Threshold column reads "no verdict" for it rather than the "—" that a
// run measured within its threshold shows.

import "@/components/ui/controls.css";

import { TableScroll } from "@/components/ui/TableScroll";
import type { SearchExecution } from "@/lib/api";
import { axisTimeLabel, formatRunDuration, resultCount, thresholdVerdict } from "@/lib/searchRuns";

function execTone(status: string): string {
  if (status === "COMPLETED") return "ok";
  if (status === "FAILED" || status === "TIMEOUT") return "crit";
  return "warn";
}

const LOWER_BOUND_NOTE =
  "Truncated: the search stopped at its row cap, so this count is a lower bound";
const NO_VERDICT_NOTE =
  "No verdict: the count is a lower bound, so the threshold cannot be judged";

function Results({ e }: { e: SearchExecution }) {
  const count = resultCount(e);
  switch (count.kind) {
    case "lower-bound":
      return (
        <span title={LOWER_BOUND_NOTE}>
          <span aria-hidden="true">≥ </span>
          <span className="sr-only">at least </span>
          {count.value}
        </span>
      );
    case "truncated-unknown":
      return (
        <span className="muted" title={LOWER_BOUND_NOTE}>
          truncated, count not recorded
        </span>
      );
    case "measured":
      return <>{count.value}</>;
    default:
      return <>—</>;
  }
}

function Threshold({ e }: { e: SearchExecution }) {
  switch (thresholdVerdict(e)) {
    case "breached":
      return <span className="pill crit">breached</span>;
    case "no-verdict":
      return (
        <span className="muted" title={NO_VERDICT_NOTE}>
          no verdict
        </span>
      );
    default:
      return <>—</>;
  }
}

export function ExecutionsTable({ executions }: { executions: SearchExecution[] }) {
  const anyTruncated = executions.some((e) => e.truncated);
  return (
    <>
      <TableScroll label="Execution history">
        <table className="table-dense">
          <caption className="sr-only">
            Executions of this search, newest first, with status, result count and threshold
            verdict. Times are UTC.
          </caption>
          <thead>
            <tr>
              <th scope="col">Started (UTC)</th>
              <th scope="col">Status</th>
              <th scope="col">Trigger</th>
              <th scope="col">Ver</th>
              <th scope="col" className="num">Duration</th>
              <th scope="col" className="num">Results</th>
              <th scope="col" className="num">Retries</th>
              <th scope="col">Threshold</th>
              <th scope="col">Error</th>
            </tr>
          </thead>
          <tbody>
            {executions.map((e) => (
              <tr key={e.id}>
                <td className="nowrap">{axisTimeLabel(e.started_at)}</td>
                <td>
                  <span className={`pill ${execTone(e.status)}`}>{e.status}</span>
                </td>
                <td>{e.trigger}</td>
                <td>v{e.query_version}</td>
                <td className="num">{formatRunDuration(e.duration_ms)}</td>
                <td className="num">
                  <Results e={e} />
                </td>
                <td className="num">{e.retry_count}</td>
                <td>
                  <Threshold e={e} />
                </td>
                <td>{e.error_type ? <span className="pill crit">{e.error_type}</span> : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      {anyTruncated && (
        <ul className="table-notes" aria-label="Truncated runs">
          <li>
            “≥” marks a truncated run: the search stopped at its row cap, so the count is a
            lower bound.
          </li>
          <li>
            “No verdict” means a truncated run did not breach its threshold at the count it
            reached; the real count may still be above it. A run measured within its threshold
            shows “—”.
          </li>
        </ul>
      )}
    </>
  );
}
