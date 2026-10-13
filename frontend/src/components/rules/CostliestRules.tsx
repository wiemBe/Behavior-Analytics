// The costliest enabled rules by QRadar's own capacity benchmark.
//
// The benchmark is how fast QRadar could evaluate the rule, so a LOWER number
// is a COSTLIER rule. It is not a firing rate and it is not derived from
// matches; the section says so before the first figure.

import Link from "next/link";

import "@/components/ui/controls.css";

import { TableScroll } from "@/components/ui/TableScroll";
import type { CostSection } from "@/lib/api";

import { formatCapacity, formatCount, kindLabel } from "./ruleStatistics";

export function CostliestRules({ cost }: { cost: CostSection }) {
  return (
    <section aria-labelledby="stats-cost">
      <div className="section-head">
        <h2 className="section-title" id="stats-cost">
          Costliest by benchmark
        </h2>
      </div>
      <p className="subtitle">{cost.label}</p>

      {cost.items.length === 0 ? (
        <div className="notice">
          <p>
            <strong>No enabled rule has a benchmark to rank.</strong>
          </p>
          <p>The API returned no non-zero capacity benchmark for an enabled rule.</p>
        </div>
      ) : (
        <TableScroll label="Costliest rules by benchmark">
          <table className="stats-table stats-cost">
            <caption className="sr-only">
              Enabled rules ordered from the lowest capacity benchmark, which is the costliest to
              evaluate. Unit: {cost.unit}.
            </caption>
            <thead>
              <tr>
                <th scope="col">Rule</th>
                <th scope="col" className="num">
                  Average capacity
                  <span className="cell-sub">{cost.unit}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {cost.items.map((r) => (
                <tr key={r.rule_id}>
                  <th scope="row" className="cell-rule">
                    <Link href={`/rules/${r.rule_id}`}>{r.name}</Link>
                    <span className="cell-sub">{kindLabel(r.rule_type, r.is_building_block)}</span>
                  </th>
                  <td className="num">{formatCapacity(r.average_capacity)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}

      <p className="table-note">
        {formatCount(cost.no_benchmark_count)} enabled{" "}
        {cost.no_benchmark_count === 1 ? "rule has" : "rules have"} no benchmark and{" "}
        {cost.no_benchmark_count === 1 ? "is" : "are"} left out of this ranking.
      </p>
    </section>
  );
}
