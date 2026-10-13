"use client";

// Assigns each raw value of an enumerated field to an outcome.
//
// The values come from the last validation, which sampled the field's most
// frequent values. A value the sample did not list is not "allowed": it counts
// as unmapped, and the editor says so whenever the list is truncated. The
// component is controlled and does not save.

import { useId, useState } from "react";

import { TableScroll } from "@/components/ui/TableScroll";
import type { ActionValueMap, DiscoveredValue } from "@/lib/api";
import {
  MISSING,
  assignValue,
  assignmentOf,
  formatCount,
  valueRows,
} from "@/lib/fieldMapping";

import "@/components/ui/controls.css";

const OUTCOME_LABEL: Record<string, string> = {
  BLOCKED: "Blocked",
  ALLOWED: "Allowed",
};

export function outcomeLabel(outcome: string): string {
  return OUTCOME_LABEL[outcome] ?? outcome;
}

function Row({
  fieldLabel,
  outcomes,
  row,
  map,
  disabled,
  onChange,
}: {
  fieldLabel: string;
  outcomes: string[];
  row: ReturnType<typeof valueRows>[number];
  map: ActionValueMap;
  disabled: boolean;
  onChange: (next: ActionValueMap) => void;
}) {
  const name = useId();
  const current = assignmentOf(map, outcomes, row.value);
  return (
    <tr>
      <th scope="row" className="fm-value-cell">
        <code>{row.value}</code>
        {!row.seen && <span className="cell-sub">not seen in the last validation</span>}
        {current === "BOTH" && <span className="cell-sub">in both lists; choose one</span>}
      </th>
      <td className="num">
        {row.events == null ? (
          <span title="Not in the last validation's list">
            {MISSING}
            <span className="sr-only"> not counted</span>
          </span>
        ) : (
          formatCount(row.events)
        )}
      </td>
      <td>
        <fieldset className="fm-radios">
          <legend className="sr-only">
            {fieldLabel} value {row.value}
          </legend>
          {outcomes.map((o) => (
            <label key={o} className="fm-radio">
              <input
                type="radio"
                name={name}
                checked={current === o}
                disabled={disabled}
                onChange={() => onChange(assignValue(map, outcomes, row.value, o))}
              />
              {outcomeLabel(o)}
            </label>
          ))}
          <label className="fm-radio">
            <input
              type="radio"
              name={name}
              checked={current === null}
              disabled={disabled}
              onChange={() => onChange(assignValue(map, outcomes, row.value, null))}
            />
            Not mapped
          </label>
        </fieldset>
      </td>
    </tr>
  );
}

export function ValueMapEditor({
  fieldLabel,
  outcomes,
  discovered,
  truncated,
  map,
  onChange,
  blockedReason,
  discoveredNote,
  disabled = false,
}: {
  fieldLabel: string;
  outcomes: string[];
  /** Null: the last validation listed no values (none run, or none found). */
  discovered: DiscoveredValue[] | null;
  truncated: boolean;
  map: ActionValueMap;
  onChange: (next: ActionValueMap) => void;
  /** When set, the property is unmapped and values cannot be assigned yet. */
  blockedReason?: string | null;
  /** Where the discovered values came from, when that differs from now. */
  discoveredNote?: string | null;
  disabled?: boolean;
}) {
  const addId = useId();
  const [extra, setExtra] = useState("");
  const rows = valueRows(discovered ?? [], map, outcomes);
  const trimmed = extra.trim();

  function addValue(outcome: string) {
    if (trimmed === "" || rows.some((r) => r.value === trimmed)) return;
    onChange(assignValue(map, outcomes, trimmed, outcome));
    setExtra("");
  }

  return (
    <div className="fm-values">
      {blockedReason && (
        <p className="fm-banner is-warn" role="status">
          {blockedReason}
        </p>
      )}
      {discoveredNote && <p className="fm-note">{discoveredNote}</p>}
      {discovered === null || discovered.length === 0 ? (
        <p className="fm-empty">
          The last validation listed no {fieldLabel.toLowerCase()} values. Validate the source
          while it has events to discover them; until then every value is unmapped.
        </p>
      ) : null}
      {truncated && (
        <p className="fm-banner is-warn" role="status">
          This list is truncated: only the most frequent {fieldLabel.toLowerCase()} values were
          returned. Values not listed count as unmapped, never as allowed.
        </p>
      )}

      {rows.length > 0 && (
        <TableScroll label={`${fieldLabel} value outcomes`}>
          <table className="table-dense">
            <caption className="sr-only">
              {fieldLabel} values with their event counts in the last validation window, and the
              outcome each is assigned to.
            </caption>
            <thead>
              <tr>
                <th scope="col">Value</th>
                <th scope="col" className="num">
                  Events
                  <span className="cell-sub">in the window</span>
                </th>
                <th scope="col">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Row
                  key={r.value}
                  fieldLabel={fieldLabel}
                  outcomes={outcomes}
                  row={r}
                  map={map}
                  disabled={disabled}
                  onChange={onChange}
                />
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}

      <div className="fm-add-value">
        <label htmlFor={addId}>Add a value that was not listed</label>
        <input
          id={addId}
          className="field fm-input"
          type="text"
          value={extra}
          disabled={disabled}
          onChange={(e) => setExtra(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
            }
          }}
        />
        {outcomes.map((o) => (
          <button
            key={o}
            type="button"
            className="action secondary"
            disabled={disabled || trimmed === ""}
            onClick={() => addValue(o)}
          >
            Add as {outcomeLabel(o).toLowerCase()}
          </button>
        ))}
      </div>
    </div>
  );
}
