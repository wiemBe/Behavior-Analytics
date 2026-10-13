"use client";

// One row per class that has a monitored source or a policy row.
//
// The mode column says where the answer comes from: a class row, or the default
// (every monitored source alerts unless an operator opts it out, D-9). The
// source count says what it counts, because the fleet summary knows enabled
// sources and only a class row knows how many are monitored.

import { useState } from "react";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { TableScroll } from "@/components/ui/TableScroll";
import {
  outagePolicyApi,
  type OutageFleetSummary,
  type OutageMode,
  type OutagePolicyRow,
} from "@/lib/api";
import { classLabel } from "@/lib/fleet";

import {
  classImpact,
  classRow,
  defaultGraceText,
  defaultMode,
  formatGrace,
  graceParts,
  modeLabel,
  overrideSplit,
  removeFailure,
  shortId,
  type ClassLine,
} from "./outage";
import { ImpactNote, PolicyForm, type PolicyBody } from "./PolicyForm";

import "@/components/ui/controls.css";

function ModePill({ mode }: { mode: OutageMode | null }) {
  if (mode === null) return <span className="muted">Not reported</span>;
  return <span className={mode === "ALERT" ? "pill info" : "pill warn"}>{modeLabel(mode)}</span>;
}

function changeFor(
  line: ClassLine,
  rows: OutagePolicyRow[],
  summary: OutageFleetSummary | null,
  bucketSeconds: number | null,
  to: { mode: OutageMode | null; grace: number | null; removal: boolean },
) {
  const split = overrideSplit(rows, line.source_class);
  return classImpact({
    source_class: line.source_class,
    fromMode: line.mode,
    fromGrace: line.grace,
    toMode: to.mode,
    toGrace: to.grace,
    covered: line.covered,
    defaultGrace: defaultGraceText(summary),
    alertingOverrides: split.alerting,
    recordOnlyOverrides: split.recordOnly,
    removal: to.removal,
    bucketSeconds,
  });
}

function ClassRow({
  line,
  rows,
  summary,
  bucketSeconds,
  editing,
  removing,
  setEditing,
  setRemoving,
  onChanged,
  onGone,
}: {
  line: ClassLine;
  rows: OutagePolicyRow[];
  summary: OutageFleetSummary | null;
  bucketSeconds: number | null;
  editing: boolean;
  removing: boolean;
  setEditing: (on: boolean) => void;
  setRemoving: (on: boolean) => void;
  onChanged: (text: string) => Promise<void>;
  onGone: (text: string) => Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  const cls = line.source_class;
  const name = classLabel(cls);
  const instanceRow = line.row !== undefined && line.row.instance_id !== null;
  const fallback = defaultMode(summary);

  async function remove() {
    if (pending || !line.row) return;
    setPending(true);
    setOutcome({ kind: "pending" });
    try {
      await outagePolicyApi.deleteClass(cls, line.row.instance_id);
      setRemoving(false);
      await onChanged(`${name} uses the default again.`);
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

  const sub = line.row
    ? instanceRow
      ? "A row for one instance"
      : "From the class row"
    : "From the default";

  return (
    <>
      <tr>
        <th scope="row" className="cell-class">
          {name}
          {instanceRow && line.row && (
            <span className="cell-sub">
              <span className="pill">
                Instance: {line.row.instance_name ?? shortId(line.row.instance_id as string)}
              </span>
            </span>
          )}
        </th>
        <td>
          <ModePill mode={line.mode} />
          <span className="cell-sub">{sub}</span>
        </td>
        <td className="nowrap">
          {line.grace === null ? (
            <span className="muted">
              Default
              {defaultGraceText(summary) && ` · ${defaultGraceText(summary)}`}
            </span>
          ) : (
            <>
              {graceParts(line.grace, bucketSeconds).buckets}
              {graceParts(line.grace, bucketSeconds).minutes && (
                <span className="cell-sub">{graceParts(line.grace, bucketSeconds).minutes}</span>
              )}
            </>
          )}
        </td>
        <td className="num">
          {line.covered === null ? (
            <>
              <span className="muted" aria-label="Not available">
                —
              </span>
              <span className="cell-sub">
                {instanceRow ? "Not counted per instance" : "Fleet summary unavailable"}
              </span>
            </>
          ) : (
            line.covered
          )}
        </td>
        <td className="num">
          {line.overrides === null ? (
            <span className="muted" aria-label="Not available">
              —
            </span>
          ) : (
            line.overrides
          )}
        </td>
        <td className="cell-actions">
          <div className="actions">
            {!instanceRow && (
              <button
                type="button"
                className="action secondary"
                aria-label={`${line.row ? "Edit" : "Set policy for"} ${name}`}
                disabled={pending}
                onClick={() => {
                  setRemoving(false);
                  setEditing(!editing);
                }}
              >
                {line.row ? "Edit" : "Set policy"}
              </button>
            )}
            {line.row && (
              <button
                type="button"
                className="action secondary"
                aria-label={`Use the default for ${name}${instanceRow ? " (instance row)" : ""}`}
                disabled={pending}
                onClick={() => {
                  setEditing(false);
                  setOutcome({ kind: "idle" });
                  setRemoving(!removing);
                }}
              >
                Use default
              </button>
            )}
          </div>
        </td>
      </tr>
      {(editing || removing) && (
        <tr className="status-row">
          <td colSpan={6}>
            {editing && (
              <PolicyForm
                label={`${line.row ? "Edit" : "Set policy for"} ${name}`}
                initialMode={line.mode ?? "ALERT"}
                initialGrace={line.grace}
                bucketSeconds={bucketSeconds}
                impactFor={(mode, grace) =>
                  changeFor(line, rows, summary, bucketSeconds, { mode, grace, removal: false })
                }
                isUnchanged={(mode, grace) =>
                  line.mode !== null && mode === line.mode && grace === line.grace
                }
                onSave={async (body: PolicyBody) => {
                  await outagePolicyApi.putClass(cls, body);
                  setEditing(false);
                  await onChanged(
                    `Saved. ${name} is now ${modeLabel(body.mode)}${
                      body.silence_grace_buckets === null
                        ? " with the default grace"
                        : `, grace ${formatGrace(body.silence_grace_buckets, bucketSeconds)}`
                    }.`,
                  );
                }}
                onCancel={() => setEditing(false)}
              />
            )}
            {removing && (
              <div className="rule-form outage-form">
                <ImpactNote
                  title="Before you use the default"
                  impact={changeFor(line, rows, summary, bucketSeconds, {
                    mode: instanceRow ? (classRow(rows, cls)?.mode ?? fallback) : fallback,
                    grace: null,
                    removal: true,
                  })}
                />
                <div className="actions">
                  <button
                    type="button"
                    className="action"
                    disabled={pending}
                    onClick={() => void remove()}
                  >
                    {pending ? "Saving…" : "Use default"}
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

export function ClassTable({
  lines,
  rows,
  summary,
  bucketSeconds,
  open,
  setOpen,
  onChanged,
  onGone,
}: {
  lines: ClassLine[];
  rows: OutagePolicyRow[];
  summary: OutageFleetSummary | null;
  bucketSeconds: number | null;
  open: { key: string; kind: "edit" | "remove" } | null;
  setOpen: (v: { key: string; kind: "edit" | "remove" } | null) => void;
  onChanged: (text: string) => Promise<void>;
  onGone: (text: string) => Promise<void>;
}) {
  if (lines.length === 0) {
    return (
      <div className="notice" role="status">
        <p>
          <strong>No class has a monitored source.</strong>
        </p>
        <p>
          The fleet summary lists no enabled source and no class row exists, so there is nothing to
          decide here yet.
        </p>
      </div>
    );
  }
  return (
    <TableScroll label="Outage policy by class">
      <table className="fleet-table outage-table">
        <caption className="sr-only">
          One row per class: the mode its silent sources resolve to, where that comes from, the
          grace, and how many sources it covers.
        </caption>
        <thead>
          <tr>
            <th scope="col">Class</th>
            <th scope="col">Outage mode</th>
            <th scope="col">Grace</th>
            <th scope="col" className="num">
              Monitored sources covered
            </th>
            <th scope="col" className="num">
              Source overrides
            </th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <ClassRow
              key={line.key}
              line={line}
              rows={rows}
              summary={summary}
              bucketSeconds={bucketSeconds}
              editing={open?.key === line.key && open.kind === "edit"}
              removing={open?.key === line.key && open.kind === "remove"}
              setEditing={(on) => setOpen(on ? { key: line.key, kind: "edit" } : null)}
              setRemoving={(on) => setOpen(on ? { key: line.key, kind: "remove" } : null)}
              onChanged={onChanged}
              onGone={onGone}
            />
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
