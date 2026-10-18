// The Threshold rules tab (design §G.13.3): the custom rules that pin a value of this watch.
//
// `threshold_rules` carries the rule's id, name, enabled flag and the id of the value it pins;
// the value's text is looked up in the values the page read. Rule names and values are
// attacker-influenced text and are rendered as text. Creating a rule is the rule editor's job
// and needs `custom_rule:write`; a reader sees the list and is told which permission adds it.

import Link from "next/link";

import type { PwPinRule, PwValue } from "@/lib/api";

import { valueText } from "./WatchValuesTable";

export type RuleAccess = "writer" | "reader" | "unknown";

export const RULE_WRITE_PERMISSION = "custom_rule:write";

export function ThresholdRules({
  rules,
  values,
  access,
}: {
  /** null: the single-watch read did not carry the list. */
  rules: PwPinRule[] | null;
  /** Every value the page could read, by id. */
  values: ReadonlyMap<string, PwValue> | null;
  access: RuleAccess;
}) {
  if (rules === null) {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>The threshold rules of this watch could not be loaded.</strong>
        </p>
        <p>
          The watch read did not carry them. Reload the page to try again; rules are listed on{" "}
          <Link href="/custom-rules">Custom rules</Link> meanwhile.
        </p>
      </div>
    );
  }
  const create =
    access === "writer" ? (
      <p>
        <Link href="/custom-rules/new" className="action secondary button-link">
          New rule on a value
        </Link>
      </p>
    ) : (
      <p className="subtitle">
        {access === "unknown"
          ? `Your permissions could not be read, so the create link is not shown. Adding a rule needs ${RULE_WRITE_PERMISSION}.`
          : `Adding a rule needs ${RULE_WRITE_PERMISSION}. Ask an admin to grant a role that includes it.`}
      </p>
    );
  return (
    <div className="stack-6">
      <p className="subtitle">
        A threshold rule is a custom rule that pins one value of this watch. A rule&apos;s breaches are triaged on{" "}
        <Link href="/alerts">Alerts</Link>.
      </p>
      {rules.length === 0 ? (
        <div className="notice" role="status">
          <p>
            <strong>No rule pins a value of this watch.</strong>
          </p>
          <p>
            Pick the property-watch value as the subject of a new custom rule to alert on one
            value&apos;s count.
          </p>
        </div>
      ) : (
        <table className="table-dense pw-rules" aria-label="Threshold rules">
          <thead>
            <tr>
              <th scope="col">Rule</th>
              <th scope="col">Pinned value</th>
              <th scope="col">State</th>
            </tr>
          </thead>
          <tbody>
            {rules.map((r) => {
              const v = values?.get(r.pw_value_id);
              return (
                <tr key={r.id}>
                  <td className="cell-name">
                    <Link href={`/custom-rules/${r.id}`}>{r.name}</Link>
                  </td>
                  <td className="pw-value-cell">
                    {v ? (
                      <span className="pw-value-text">{valueText(v)}</span>
                    ) : (
                      <span className="muted">
                        not among the tracked values read; it may no longer be stored
                      </span>
                    )}
                  </td>
                  <td>
                    <span className={`pill pill-quiet ${r.enabled ? "ok" : ""}`}>
                      {r.enabled ? "Enabled" : "Disabled"}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {create}
    </div>
  );
}
