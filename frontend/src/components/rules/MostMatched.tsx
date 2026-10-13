// The rules that carry the matching load, ranked by matched events (default)
// or by match records. The rate is matched events per second over the seconds
// that were measured, so a half-collected span does not read as a low rate.
//
// A `null` list is a measurement that does not exist; its reason is shown and
// it is never drawn as an empty ranking.

import Link from "next/link";

import "@/components/ui/controls.css";

import { TableScroll } from "@/components/ui/TableScroll";
import type { MatchRankRow } from "@/lib/api";

import {
  formatCount,
  formatRate,
  kindLabel,
  LOWER_BOUND_MEANING,
  statsHref,
  typeLabel,
  typeTitle,
  type Rank,
  type StatsQuery,
} from "./ruleStatistics";

const RANKS: { value: Rank; label: string }[] = [
  { value: "events", label: "Matched events" },
  { value: "matches", label: "Match records" },
];

function Bound({ lower, children }: { lower: boolean; children: string }) {
  return lower ? <span>≥&nbsp;{children}</span> : <>{children}</>;
}

export function MostMatched({
  rows,
  reason,
  query,
}: {
  /** The list for `query.rank`; null when matches could not be measured. */
  rows: MatchRankRow[] | null;
  reason: string | null;
  query: StatsQuery;
}) {
  const anyLower = (rows ?? []).some((r) => r.lower_bound);

  return (
    <section aria-labelledby="stats-top">
      <div className="section-head row between">
        <h2 className="section-title" id="stats-top">
          Most matched
        </h2>
        <nav className="range-selector" aria-label="Rank by">
          <span className="muted">Rank by</span>
          {RANKS.map((r) => (
            <Link
              key={r.value}
              href={statsHref({ ...query, rank: r.value })}
              className={`range-option${r.value === query.rank ? " range-option-active" : ""}`}
              aria-current={r.value === query.rank ? "page" : undefined}
            >
              {r.label}
            </Link>
          ))}
        </nav>
      </div>

      {rows === null ? (
        <div className="banner" role="status">
          <div>
            <p>
              <strong>{reason ?? "No complete window exists to measure rule matches."}</strong>
            </p>
            <p>No ranking is shown, and no rule is reported as quiet.</p>
          </div>
        </div>
      ) : rows.length === 0 ? (
        <div className="notice">
          <p>
            <strong>No rule matched in the measured windows.</strong>
          </p>
        </div>
      ) : (
        <>
          {reason && (
            <p className="table-note stats-top-reason">
              <strong>PARTIAL.</strong> {reason}
            </p>
          )}
          <TableScroll label="Most matched rules">
            <table className="stats-table stats-top">
              <caption className="sr-only">
                Rules ranked by {query.rank === "events" ? "matched events" : "match records"} over
                the measured windows, with the match rate in events per second.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Rule</th>
                  <th scope="col" className="stats-wide-fact">
                    Type
                  </th>
                  <th scope="col" className="stats-wide-fact">
                    Building block
                  </th>
                  <th scope="col" className="num">
                    Matched events
                  </th>
                  <th scope="col" className="num">
                    Match records
                  </th>
                  <th scope="col" className="num">
                    Match rate (events/s)
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.rule_id}>
                    <th scope="row" className="cell-rule">
                      <Link href={`/rules/${r.rule_id}`}>{r.name}</Link>
                      <span className="cell-sub stats-narrow-fact">
                        {kindLabel(r.rule_type, r.is_building_block)}
                        {r.enabled ? "" : ", disabled"}
                      </span>
                    </th>
                    <td className="stats-wide-fact">
                      <span title={typeTitle(r.rule_type)}>{typeLabel(r.rule_type)}</span>
                    </td>
                    <td className="stats-wide-fact">{r.is_building_block ? "Yes" : "No"}</td>
                    <td className="num">
                      <Bound lower={r.lower_bound}>{formatCount(r.matched_events)}</Bound>
                    </td>
                    <td className="num">
                      <Bound lower={r.lower_bound}>{formatCount(r.match_records)}</Bound>
                    </td>
                    <td className="num">
                      <Bound lower={r.lower_bound}>{formatRate(r.match_rate_per_second)}</Bound>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          {anyLower && (
            <p className="table-note">
              <strong>≥</strong> marks a lower bound. {LOWER_BOUND_MEANING}
            </p>
          )}
        </>
      )}
    </section>
  );
}
