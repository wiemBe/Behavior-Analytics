// Rule counts as a pivot: rule type by enabled and disabled, rules first and
// building blocks after. Origin is a filter, because the counts are the same
// rules seen through a different lens, not a different table.

import Link from "next/link";

import "@/components/ui/controls.css";

import { TableScroll } from "@/components/ui/TableScroll";
import type { RuleCountRow } from "@/lib/api";

import {
  ALL_ORIGINS,
  formatCount,
  originFilterLabel,
  originsPresent,
  pivotCounts,
  statsHref,
  typeLabel,
  typeTitle,
  type StatsQuery,
} from "./ruleStatistics";

export function RuleComposition({
  counts,
  query,
}: {
  counts: RuleCountRow[];
  query: StatsQuery;
}) {
  const origins = originsPresent(counts);
  const groups = pivotCounts(counts, query.origin);

  return (
    <section aria-labelledby="stats-composition">
      <div className="section-head row between">
        <h2 className="section-title" id="stats-composition">
          Rule composition
        </h2>
        {origins.length > 0 && (
          <nav className="range-selector" aria-label="Origin">
            <span className="muted">Origin</span>
            {[ALL_ORIGINS, ...origins].map((o) => (
              <Link
                key={o}
                href={statsHref({ ...query, origin: o })}
                className={`range-option${o === query.origin ? " range-option-active" : ""}`}
                aria-current={o === query.origin ? "page" : undefined}
              >
                {originFilterLabel(o)}
              </Link>
            ))}
          </nav>
        )}
      </div>

      {groups.length === 0 ? (
        <div className="notice">
          <p>
            <strong>No rules are collected for this origin.</strong>
          </p>
          <p>
            Choose another origin, or run a rule sync (<code>python -m app.cli.sync rules</code>)
            to collect rules from QRadar.
          </p>
        </div>
      ) : (
        <TableScroll label="Rule composition">
          <table className="stats-table stats-composition">
            <caption className="sr-only">
              Rules and building blocks by type, split into enabled and disabled
              {query.origin === ALL_ORIGINS ? "" : `, for origin ${originFilterLabel(query.origin)}`}
              .
            </caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col" className="num">
                  Enabled
                </th>
                <th scope="col" className="num">
                  Disabled
                </th>
                <th scope="col" className="num">
                  Total
                </th>
              </tr>
            </thead>
            {groups.map((g) => (
              <tbody key={g.buildingBlocks ? "bb" : "rules"}>
                <tr className="stats-group">
                  <th scope="rowgroup" colSpan={4}>
                    {g.buildingBlocks ? "Building blocks" : "Rules"}
                  </th>
                </tr>
                {g.rows.map((r) => (
                  <tr key={r.type ?? "null"}>
                    <th scope="row">
                      <span title={typeTitle(r.type)}>{typeLabel(r.type)}</span>
                    </th>
                    <td className="num">{formatCount(r.enabled)}</td>
                    <td className="num">{formatCount(r.disabled)}</td>
                    <td className="num">{formatCount(r.total)}</td>
                  </tr>
                ))}
                <tr className="stats-total">
                  <th scope="row">{g.buildingBlocks ? "All building blocks" : "All rules"}</th>
                  <td className="num">{formatCount(g.totals.enabled)}</td>
                  <td className="num">{formatCount(g.totals.disabled)}</td>
                  <td className="num">{formatCount(g.totals.total)}</td>
                </tr>
              </tbody>
            ))}
          </table>
        </TableScroll>
      )}
    </section>
  );
}
