"use client";

// The source overrides: every SOURCE row, who set it and when.
//
// A row's source is named from the API's `source_name`; a bare id is never the
// name. A row whose source has left the inventory says so and offers only
// removal, because there is nothing left to edit.

import { useState } from "react";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { TableScroll } from "@/components/ui/TableScroll";
import { outagePolicyApi, type OutageMode, type OutagePolicyRow } from "@/lib/api";
import { classLabel } from "@/lib/fleet";
import { UtcTime } from "@/components/ui/UtcTime";

import {
  graceParts,
  isGone,
  modeLabel,
  removeFailure,
  shortId,
  sourceImpact,
} from "./outage";
import { ImpactNote, PolicyForm } from "./PolicyForm";

import "@/components/ui/controls.css";

export const NO_LONGER_IN_INVENTORY = "Source no longer in inventory";

function SourceRow({
  row,
  classMode,
  bucketSeconds,
  defaultGrace,
  editing,
  removing,
  setEditing,
  setRemoving,
  onChanged,
  onGone,
}: {
  row: OutagePolicyRow;
  /** The mode this source's class gives it, absent the override; null when unknown. */
  classMode: OutageMode | null;
  bucketSeconds: number | null;
  defaultGrace: string | null;
  editing: boolean;
  removing: boolean;
  setEditing: (on: boolean) => void;
  setRemoving: (on: boolean) => void;
  onChanged: (text: string) => Promise<void>;
  onGone: (text: string) => Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  const gone = isGone(row);
  const name = gone ? NO_LONGER_IN_INVENTORY : (row.source_name as string);
  const instanceRow = row.instance_id !== null;

  async function remove() {
    if (pending) return;
    setPending(true);
    setOutcome({ kind: "pending" });
    try {
      await outagePolicyApi.deleteSource(row.scope_value, row.instance_id);
      setRemoving(false);
      await onChanged(
        gone
          ? "Removed the override of a source that is no longer in the inventory."
          : `Removed the override of ${name}. It follows its class again.`,
      );
    } catch (err) {
      const failure = removeFailure(err);
      if (failure.gone) {
        setRemoving(false);
        await onGone(failure.text);
      } else {
        setOutcome({ kind: failure.kind, text: failure.text });
      }
    } finally {
      setPending(false);
    }
  }

  const change = (mode: OutageMode, grace: number | null, removal: boolean) =>
    sourceImpact({
      name,
      source_class: row.source_class,
      classMode,
      fromMode: row.mode,
      fromGrace: row.silence_grace_buckets,
      toMode: mode,
      toGrace: grace,
      removal,
      bucketSeconds,
      defaultGrace,
    });

  return (
    <>
      <tr id={`override-${row.scope_value}${instanceRow ? `-${row.instance_id}` : ""}`}>
        <th scope="row" className="cell-source">
          {gone ? <span className="muted">{name}</span> : name}
          <span className="cell-sub">
            {gone
              ? `Log source id ${shortId(row.scope_value)}…`
              : row.source_class
                ? classLabel(row.source_class)
                : ""}
          </span>
          {instanceRow && row.instance_id && (
            <span className="cell-sub">
              <span className="pill">
                Instance: {row.instance_name ?? shortId(row.instance_id)}
              </span>
            </span>
          )}
        </th>
        <td>
          <span className={row.mode === "ALERT" ? "pill info" : "pill warn"}>
            {modeLabel(row.mode)}
          </span>
        </td>
        <td className="nowrap">
          {row.silence_grace_buckets === null ? (
            <span className="muted">Default</span>
          ) : (
            <>
              {graceParts(row.silence_grace_buckets, bucketSeconds).buckets}
              {graceParts(row.silence_grace_buckets, bucketSeconds).minutes && (
                <span className="cell-sub">
                  {graceParts(row.silence_grace_buckets, bucketSeconds).minutes}
                </span>
              )}
            </>
          )}
        </td>
        <td>
          {row.updated_by ?? <span className="muted">—</span>}
          <UtcTime value={row.updated_at} variant="compact" className="cell-sub nowrap" />
        </td>
        <td className="cell-actions">
          <div className="actions">
            {!gone && !instanceRow && (
              <button
                type="button"
                className="action secondary"
                aria-label={`Edit the override of ${name}`}
                disabled={pending}
                onClick={() => {
                  setRemoving(false);
                  setEditing(!editing);
                }}
              >
                Edit
              </button>
            )}
            <button
              type="button"
              className="action secondary"
              aria-label={`Remove the override of ${name}${gone ? ` (id ${shortId(row.scope_value)})` : ""}`}
              disabled={pending}
              onClick={() => {
                setEditing(false);
                setOutcome({ kind: "idle" });
                setRemoving(!removing);
              }}
            >
              Remove override
            </button>
          </div>
        </td>
      </tr>
      {(editing || removing) && (
        <tr className="status-row">
          <td colSpan={5}>
            {editing && (
              <PolicyForm
                label={`Edit the override of ${name}`}
                initialMode={row.mode}
                initialGrace={row.silence_grace_buckets}
                bucketSeconds={bucketSeconds}
                impactFor={(mode, grace) => change(mode, grace, false)}
                isUnchanged={(mode, grace) =>
                  mode === row.mode && grace === row.silence_grace_buckets
                }
                onSave={async (body) => {
                  await outagePolicyApi.putSource(row.scope_value, body);
                  setEditing(false);
                  await onChanged(`Saved. ${name} is now ${modeLabel(body.mode)}.`);
                }}
                onCancel={() => setEditing(false)}
              />
            )}
            {removing && (
              <div className="rule-form outage-form">
                <ImpactNote
                  title="Before you remove"
                  impact={
                    gone
                      ? {
                          known: true,
                          lines: [
                            "This row governs no source: its source is no longer in the inventory. Removing it changes no behaviour.",
                          ],
                        }
                      : change(row.mode, null, true)
                  }
                />
                <div className="actions">
                  <button
                    type="button"
                    className="action"
                    disabled={pending}
                    onClick={() => void remove()}
                  >
                    {pending ? "Removing…" : "Remove override"}
                  </button>
                  <button
                    type="button"
                    className="action secondary"
                    disabled={pending}
                    onClick={() => setRemoving(false)}
                  >
                    Keep
                  </button>
                </div>
                <ChangeStatus outcome={outcome} />
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export function SourceTable({
  rows,
  classModeOf,
  bucketSeconds,
  defaultGrace,
  open,
  setOpen,
  onChanged,
  onGone,
}: {
  rows: OutagePolicyRow[];
  classModeOf: (cls: string | null) => OutageMode | null;
  bucketSeconds: number | null;
  defaultGrace: string | null;
  open: { key: string; kind: "edit" | "remove" } | null;
  setOpen: (v: { key: string; kind: "edit" | "remove" } | null) => void;
  onChanged: (text: string) => Promise<void>;
  onGone: (text: string) => Promise<void>;
}) {
  return (
    <TableScroll label="Source overrides">
      <table className="fleet-table outage-table">
        <caption className="sr-only">
          Sources whose outage mode or grace is set on the source itself, and who last changed
          each. Times are UTC.
        </caption>
        <thead>
          <tr>
            <th scope="col">Source</th>
            <th scope="col">Outage mode</th>
            <th scope="col">Grace</th>
            <th scope="col">Changed by</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const key = `${row.scope_value}:${row.instance_id ?? ""}`;
            return (
              <SourceRow
                key={key}
                row={row}
                classMode={classModeOf(row.source_class)}
                bucketSeconds={bucketSeconds}
                defaultGrace={defaultGrace}
                editing={open?.key === key && open.kind === "edit"}
                removing={open?.key === key && open.kind === "remove"}
                setEditing={(on) => setOpen(on ? { key, kind: "edit" } : null)}
                setRemoving={(on) => setOpen(on ? { key, kind: "remove" } : null)}
                onChanged={onChanged}
                onGone={onGone}
              />
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}
