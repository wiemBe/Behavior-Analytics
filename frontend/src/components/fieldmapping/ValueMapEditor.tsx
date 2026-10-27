"use client";

// Assigns each raw value of an enumerated field to an outcome.
//
// The values come from the last validation, which sampled the field's most
// frequent values. A value the sample did not list is not "allowed": it counts
// as unmapped, and the editor says so whenever the list is truncated. The
// component is controlled and does not save.

import { useId, useState } from "react";
import { useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import type { ActionValueMap, DiscoveredValue } from "@/lib/api";
import {
  MISSING,
  assignValue,
  assignmentOf,
  formatCount,
  valueRows,
} from "@/lib/fieldMapping";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

import "@/components/ui/controls.css";

const OUTCOME_LABEL: Record<string, string> = {
  BLOCKED: "Blocked",
  ALLOWED: "Allowed",
};

/** An outcome's words: English without a translator, the catalog's with one; an unknown code as is. */
export function outcomeLabel(outcome: string, i18n?: BehaviorI18n): string {
  if (!Object.prototype.hasOwnProperty.call(OUTCOME_LABEL, outcome)) return outcome;
  return i18n ? i18n.t(`fieldMapping.values.${outcome}`) : OUTCOME_LABEL[outcome];
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
  const t = useTranslations("fieldMapping.values");
  const i18n = useBehaviorI18n();
  const name = useId();
  const current = assignmentOf(map, outcomes, row.value);
  return (
    <tr>
      <th scope="row" className="fm-value-cell">
        <code>{row.value}</code>
        {!row.seen && <span className="cell-sub">{t("notSeen")}</span>}
        {current === "BOTH" && <span className="cell-sub">{t("inBoth")}</span>}
      </th>
      <td className="num">
        {row.events == null ? (
          <span title={t("notInList")}>
            {MISSING}
            <span className="sr-only">{t("notCounted")}</span>
          </span>
        ) : (
          formatCount(row.events, i18n.locale)
        )}
      </td>
      <td>
        <fieldset className="fm-radios">
          <legend className="sr-only">{t("legend", { field: fieldLabel, value: row.value })}</legend>
          {outcomes.map((o) => (
            <label key={o} className="fm-radio">
              <input
                type="radio"
                name={name}
                checked={current === o}
                disabled={disabled}
                onChange={() => onChange(assignValue(map, outcomes, row.value, o))}
              />
              {outcomeLabel(o, i18n)}
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
            {t("notMapped")}
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
  truncatedNote,
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
  /** Replaces the default truncation banner when a domain has more to say; shown once. */
  truncatedNote?: string | null;
  disabled?: boolean;
}) {
  const t = useTranslations("fieldMapping.values");
  const i18n = useBehaviorI18n();
  const lowered = fieldLabel.toLocaleLowerCase(i18n.locale === "tr" ? "tr-TR" : "en-US");
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
        <p className="fm-empty">{t("empty", { field: lowered })}</p>
      ) : null}
      {truncated && (
        <p className="fm-banner is-warn" role="status">
          {truncatedNote ?? t("truncated", { field: lowered })}
        </p>
      )}

      {rows.length > 0 && (
        <TableScroll label={t("tableLabel", { field: fieldLabel })}>
          <table className="table-dense">
            <caption className="sr-only">{t("caption", { field: fieldLabel })}</caption>
            <thead>
              <tr>
                <th scope="col">{t("value")}</th>
                <th scope="col" className="num">
                  {t("events")}
                  <span className="cell-sub">{t("inWindow")}</span>
                </th>
                <th scope="col">{t("outcome")}</th>
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
        <label htmlFor={addId}>{t("addLabel")}</label>
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
            {t("addAs", {
              outcome: outcomeLabel(o, i18n).toLocaleLowerCase(
                i18n.locale === "tr" ? "tr-TR" : "en-US",
              ),
            })}
          </button>
        ))}
      </div>
    </div>
  );
}
