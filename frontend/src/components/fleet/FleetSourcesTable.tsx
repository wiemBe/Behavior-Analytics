"use client";

// The classification review table.
//
// Each row owns its source: an override or a review replaces the row with the
// API's answer, so the class, basis and review status shown are the ones the
// platform now uses, not what the click assumed. Permissions are never judged
// here; a 403 is shown with the permission it names.

import { useId, useState } from "react";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { TableScroll } from "@/components/ui/TableScroll";
import { fleetApi, type ClassificationRule, type FleetSource, type SourceClass } from "@/lib/api";
import {
  ASSIGNABLE_CLASSES,
  basisLabel,
  classLabel,
  classTone,
  conflictText,
  originLabel,
  reviewLabel,
  sourceFailure,
} from "@/lib/fleet";
import { UtcTime } from "@/components/ui/UtcTime";

import "@/components/ui/controls.css";

function SourceRow({
  initial,
  rules,
}: {
  initial: FleetSource;
  rules: Map<string, ClassificationRule>;
}) {
  const [source, setSource] = useState(initial);
  const [editing, setEditing] = useState(false);
  const [choice, setChoice] = useState<SourceClass>(
    initial.override_class ?? (initial.source_class === "UNCLASSIFIED" ? "OTHER" : initial.source_class),
  );
  const [pending, setPending] = useState<"override" | "review" | null>(null);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  const selectId = useId();

  const rule = source.rule_id ? rules.get(source.rule_id) : undefined;
  const conflict = conflictText(source);
  const reviewed = source.review_status === "REVIEWED";

  async function applyOverride(next: SourceClass | null) {
    if (pending !== null) return;
    setPending("override");
    setOutcome({ kind: "pending" });
    try {
      const updated = await fleetApi.setOverride(source.id, next);
      setSource(updated);
      setEditing(false);
      setOutcome({
        kind: "ok",
        text:
          next === null
            ? `Override cleared. Class is now ${classLabel(updated.source_class)} (${basisLabel(updated.basis).toLowerCase()}).`
            : `Saved. Class is now ${classLabel(updated.source_class)}.`,
      });
    } catch (err) {
      setOutcome(sourceFailure(err));
    } finally {
      setPending(null);
    }
  }

  async function markReviewed() {
    // A second click while the first is in flight, or on a reviewed row, must
    // not send a second request.
    if (pending !== null || reviewed) return;
    setPending("review");
    setOutcome({ kind: "pending" });
    try {
      const updated = await fleetApi.markReviewed(source.id);
      setSource(updated);
      setOutcome({ kind: "ok", text: "Marked reviewed." });
    } catch (err) {
      setOutcome(sourceFailure(err));
    } finally {
      setPending(null);
    }
  }

  return (
    <tr>
      <th scope="row" className="cell-source">
        {source.name}
        <span className="cell-sub">
          {source.type_name ?? "Type not recorded"}
          {source.enabled ? "" : " · disabled"}
        </span>
      </th>
      <td className="cell-classification">
        <span className={`pill ${classTone(source.source_class)}`}>
          {classLabel(source.source_class)}
        </span>
        <span className="cell-sub">
          {basisLabel(source.basis)}
          {source.override_by ? ` · set by ${source.override_by}` : ""}
        </span>
        {rule && (
          <span className="cell-sub">
            <code className="rule-pattern">{rule.pattern}</code>
          </span>
        )}
        {conflict && (
          <span className="cell-sub conflict-text">
            <strong>Conflict:</strong> {conflict}
          </span>
        )}
      </td>
      <td className="num">
        <UtcTime value={source.first_seen_at} variant="compact" className="nowrap" />
        <span className="cell-sub">{originLabel(source.auto_discovered)}</span>
      </td>
      <td>
        <span className={`pill pill-quiet ${source.review_status === "NEW" ? "warn" : ""}`}>
          {reviewLabel(source.review_status)}
        </span>
      </td>
      <td className="cell-actions">
        {editing ? (
          <div className="override-editor">
            <label htmlFor={selectId} className="muted">
              Class for {source.name}
            </label>
            <select
              id={selectId}
              className="select-control"
              value={choice}
              disabled={pending !== null}
              onChange={(e) => setChoice(e.target.value as SourceClass)}
            >
              {ASSIGNABLE_CLASSES.map((c) => (
                <option key={c} value={c}>
                  {classLabel(c)}
                </option>
              ))}
            </select>
            <div className="actions">
              <button
                type="button"
                className="action"
                disabled={pending !== null}
                onClick={() => void applyOverride(choice)}
              >
                Apply
              </button>
              <button
                type="button"
                className="action secondary"
                disabled={pending !== null}
                onClick={() => setEditing(false)}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="actions">
            <button
              type="button"
              className="action secondary"
              aria-label={`Set class for ${source.name}`}
              disabled={pending !== null}
              onClick={() => {
                setOutcome({ kind: "idle" });
                setEditing(true);
              }}
            >
              Set class
            </button>
            {source.override_class !== null && (
              <button
                type="button"
                className="action secondary"
                aria-label={`Clear override for ${source.name}`}
                disabled={pending !== null}
                onClick={() => void applyOverride(null)}
              >
                Clear override
              </button>
            )}
            {!reviewed && (
              <button
                type="button"
                className="action secondary"
                aria-label={`Mark ${source.name} reviewed`}
                disabled={pending !== null}
                onClick={() => void markReviewed()}
              >
                Mark reviewed
              </button>
            )}
          </div>
        )}
        <ChangeStatus outcome={outcome} />
      </td>
    </tr>
  );
}

export function FleetSourcesTable({
  items,
  rules = [],
}: {
  items: FleetSource[];
  rules?: ClassificationRule[];
}) {
  const ruleById = new Map(rules.map((r) => [r.id, r]));
  return (
    <TableScroll label="Log sources and their classification">
      <table className="fleet-table">
        <caption className="sr-only">
          Log sources with their class, the basis for it, any conflict between rules, when the
          platform first saw them and their review status.
        </caption>
        <thead>
          <tr>
            <th scope="col">Source</th>
            <th scope="col">Class and basis</th>
            <th scope="col" className="num">
              First seen
            </th>
            <th scope="col">Review</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <SourceRow key={s.id} initial={s} rules={ruleById} />
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
