// Enabled rules with a measured zero over a complete span.
//
// Silence is a claim, so the section says what it rests on: only rules whose
// matches an event search sees in full are judged, and the count of those that
// were not judged is stated beside the list. A `null` section is a judgement
// that was not made and is never drawn as "0 silent rules".

import Link from "next/link";

import "@/components/ui/controls.css";

import { TableScroll } from "@/components/ui/TableScroll";
import type { SilentSection } from "@/lib/api";
import { formatDateTime } from "@/lib/health";

import {
  formatCount,
  kindLabel,
  originLabel,
  typeLabel,
  typeTitle,
} from "./ruleStatistics";

const EVIDENCE_TEXT: Record<string, string> = {
  LOWER_BOUND: "silence unknown, matches are a lower bound",
  NOT_MEASURED: "matches not measured",
};

function evidenceText(evidence: string): string {
  return EVIDENCE_TEXT[evidence] ?? evidence.replace(/_/g, " ").toLowerCase();
}

function Instant({ value, missing }: { value: string | null; missing: string }) {
  if (!value) return <span className="muted">{missing}</span>;
  return <time dateTime={value}>{formatDateTime(value)}</time>;
}

export function SilentRules({
  silent,
  reason,
}: {
  silent: SilentSection | null;
  reason: string | null;
}) {
  return (
    <section aria-labelledby="stats-silent">
      <div className="section-head">
        <h2 className="section-title" id="stats-silent">
          Silent enabled rules
        </h2>
      </div>

      {silent === null ? (
        <div className="banner" role="status">
          <div>
            <p>
              <strong>Silence was not judged.</strong> This is not a count of zero: no enabled rule
              is reported as silent.
            </p>
            <p className="stats-window-reason">{reason ?? "The API gave no reason."}</p>
          </div>
        </div>
      ) : (
        <>
          <p className="subtitle">
            {silent.items_truncated
              ? `Showing ${formatCount(silent.items.length)} of ${formatCount(silent.total)} enabled rules with no match in the measured windows.`
              : silent.total === 0
                ? "No judged enabled rule was silent in the measured windows."
                : `${formatCount(silent.total)} enabled ${silent.total === 1 ? "rule" : "rules"} with no match in the measured windows.`}{" "}
            A rule first seen recently has had little time to match; check its first-seen time.
          </p>

          {silent.items.length > 0 && (
            <TableScroll label="Silent enabled rules">
              <table className="stats-table stats-silent">
                <caption className="sr-only">
                  Enabled rules with a measured zero over a complete span, with the time each was
                  first seen and last fired, in UTC.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Rule</th>
                    <th scope="col" className="stats-wide-fact">
                      Type
                    </th>
                    <th scope="col">First seen</th>
                    <th scope="col">Last fired</th>
                  </tr>
                </thead>
                <tbody>
                  {silent.items.map((r) => (
                    <tr key={r.rule_id}>
                      <th scope="row" className="cell-rule">
                        <Link href={`/rules/${r.rule_id}`}>{r.name}</Link>
                        <span className="cell-sub stats-narrow-fact">
                          {kindLabel(r.rule_type, r.is_building_block)}, {originLabel(r.origin)}
                        </span>
                      </th>
                      <td className="stats-wide-fact">
                        <span title={typeTitle(r.rule_type)}>{typeLabel(r.rule_type)}</span>
                        <span className="cell-sub">{originLabel(r.origin)}</span>
                      </td>
                      <td>
                        <Instant value={r.first_seen_at} missing="not recorded" />
                      </td>
                      <td>
                        <Instant value={r.last_fired_at} missing="not observed" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}

          {silent.excluded_count > 0 && (
            <div className="stats-excluded">
              <p>
                <strong>
                  {formatCount(silent.excluded_count)} enabled{" "}
                  {silent.excluded_count === 1 ? "rule was" : "rules were"} not judged,
                </strong>{" "}
                because {lowerFirst(silent.excluded_reason)}
              </p>
              <div className="stats-excluded-detail">
                <h3>Not judged, by type</h3>
                <TableScroll label="Not judged, by type">
                <table className="stats-table stats-excluded-table">
                  <caption className="sr-only">Enabled rules that were not judged, by type.</caption>
                  <thead>
                    <tr>
                      <th scope="col">Type</th>
                      <th scope="col">Why</th>
                      <th scope="col" className="num">
                        Rules
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {silent.excluded.map((x) => (
                      <tr key={`${x.rule_type ?? "null"}:${x.evidence}`}>
                        <th scope="row">
                          <span title={typeTitle(x.rule_type)}>{typeLabel(x.rule_type)}</span>
                        </th>
                        <td>{evidenceText(x.evidence)}</td>
                        <td className="num">{formatCount(x.count)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </TableScroll>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

// Lower-cases the first letter so the reason reads on after "because", but leaves an
// initialism alone: "FLOW rules ..." must not become "fLOW rules ...".
function lowerFirst(s: string): string {
  if (s.length === 0 || (s.length > 1 && s.charAt(1) === s.charAt(1).toUpperCase() && /[A-Za-z]/.test(s.charAt(1)))) {
    return s;
  }
  return s.charAt(0).toLowerCase() + s.slice(1);
}
