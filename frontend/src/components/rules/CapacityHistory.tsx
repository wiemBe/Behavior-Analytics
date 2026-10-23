// QRadar's capacity benchmark for one rule over time.
//
// A sample exists only when QRadar re-benchmarked the rule, so the history is
// sparse by construction and its length says nothing about activity. Empty is
// a fact about QRadar, not a fault. A failed request is a different state and
// is worded as one.
//
// No chart: the samples are few, irregular and not a series with a cadence, so
// a table is the honest form. Revisit if a rule ever carries a long history.

import "@/components/ui/controls.css";

import { TableScroll } from "@/components/ui/TableScroll";
import type { CapacitySample } from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";

import { formatCapacity } from "./ruleStatistics";

export const CAPACITY_UNIT = "millions of events per second (QRadar's unit)";

function Figure({ value }: { value: number | null }) {
  return value === null ? (
    <span className="muted" aria-label="Not reported">
      —
    </span>
  ) : (
    <>{formatCapacity(value)}</>
  );
}

export function CapacityHistory({
  samples,
}: {
  /** Null when the request failed; an empty list when QRadar never re-benchmarked. */
  samples: CapacitySample[] | null;
}) {
  return (
    <section aria-labelledby="rule-capacity">
      <h3 id="rule-capacity">Capacity history</h3>
      {samples === null ? (
        <div className="notice" role="alert">
          <p>
            <strong>The capacity history could not be loaded.</strong>
          </p>
          <p>
            Nothing was changed, and this does not mean the rule has no history. Reload the page;
            if it keeps failing, check that the backend is reachable.
          </p>
        </div>
      ) : samples.length === 0 ? (
        <div className="notice">
          <p>
            <strong>QRadar has not re-benchmarked this rule since collection began.</strong>
          </p>
          <p>
            A sample is stored only when QRadar reports a new benchmark time. This is not an error.
          </p>
        </div>
      ) : (
        <>
          <p className="subtitle">
            QRadar&rsquo;s capacity benchmark: how fast the rule could be evaluated, so a lower
            figure is costlier. It is not a firing rate. Newest first, in UTC. Unit: {CAPACITY_UNIT}.
          </p>
          <TableScroll label="Capacity history">
            <table className="stats-table stats-capacity">
              <caption className="sr-only">
                Capacity benchmarks for this rule, newest first.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Benchmarked</th>
                  <th scope="col" className="num">
                    Average capacity
                  </th>
                  <th scope="col" className="num">
                    Base capacity
                  </th>
                  <th scope="col" className="num">
                    Host id
                  </th>
                </tr>
              </thead>
              <tbody>
                {samples.map((s) => (
                  <tr key={s.capacity_timestamp}>
                    <th scope="row">
                      <UtcTime value={s.capacity_timestamp} />
                    </th>
                    <td className="num">
                      <Figure value={s.average_capacity} />
                    </td>
                    <td className="num">
                      <Figure value={s.base_capacity} />
                    </td>
                    <td className="num">
                      {s.base_host_id === null ? (
                        <span className="muted" aria-label="Not reported">
                          —
                        </span>
                      ) : (
                        s.base_host_id
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        </>
      )}
    </section>
  );
}
