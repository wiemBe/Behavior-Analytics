"use client";

// One row per class that has a monitored source or a policy row.
//
// The mode column says where the answer comes from: a class row, or the default
// (every monitored source alerts unless an operator opts it out, D-9). The
// source count says what it counts, because the fleet summary knows enabled
// sources and only a class row knows how many are monitored.

import { useState } from "react";
import { useTranslations } from "next-intl";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { TableScroll } from "@/components/ui/TableScroll";
import {
  outagePolicyApi,
  type OutageFleetSummary,
  type OutageMode,
  type OutagePolicyRow,
} from "@/lib/api";
import { classLabel } from "@/lib/fleet";
import { plainCount } from "@/lib/figureText";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

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
  const t = useTranslations("outage.classes");
  const i18n = useBehaviorI18n();
  if (mode === null) return <span className="muted">{t("notReported")}</span>;
  return (
    <span className={mode === "ALERT" ? "pill info" : "pill warn"}>{modeLabel(mode, i18n)}</span>
  );
}

function changeFor(
  line: ClassLine,
  rows: OutagePolicyRow[],
  summary: OutageFleetSummary | null,
  bucketSeconds: number | null,
  to: { mode: OutageMode | null; grace: number | null; removal: boolean },
  i18n: BehaviorI18n,
) {
  const split = overrideSplit(rows, line.source_class);
  return classImpact(
    {
      source_class: line.source_class,
      fromMode: line.mode,
      fromGrace: line.grace,
      toMode: to.mode,
      toGrace: to.grace,
      covered: line.covered,
      defaultGrace: defaultGraceText(summary, i18n),
      alertingOverrides: split.alerting,
      recordOnlyOverrides: split.recordOnly,
      removal: to.removal,
      bucketSeconds,
    },
    i18n,
  );
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
  const t = useTranslations("outage.classes");
  const i18n = useBehaviorI18n();
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  const cls = line.source_class;
  const name = classLabel(cls, { say: i18n.t, locale: i18n.locale });
  const instanceRow = line.row !== undefined && line.row.instance_id !== null;
  const fallback = defaultMode(summary);

  async function remove() {
    if (pending || !line.row) return;
    setPending(true);
    setOutcome({ kind: "pending" });
    try {
      await outagePolicyApi.deleteClass(cls, line.row.instance_id);
      setRemoving(false);
      await onChanged(t("usesDefault", { name }));
    } catch (err) {
      const failure = removeFailure(err, i18n);
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
      ? t("fromInstance")
      : t("fromClass")
    : t("fromDefault");
  const defaultGrace = defaultGraceText(summary, i18n);

  return (
    <>
      <tr>
        <th scope="row" className="cell-class">
          {name}
          {instanceRow && line.row && (
            <span className="cell-sub">
              <span className="pill">
                {t("instance", {
                  name: line.row.instance_name ?? shortId(line.row.instance_id as string),
                })}
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
              {defaultGrace ? t("defaultWith", { default: defaultGrace }) : t("default")}
            </span>
          ) : (
            <>
              {graceParts(line.grace, bucketSeconds, i18n).buckets}
              {graceParts(line.grace, bucketSeconds, i18n).minutes && (
                <span className="cell-sub">
                  {graceParts(line.grace, bucketSeconds, i18n).minutes}
                </span>
              )}
            </>
          )}
        </td>
        <td className="num">
          {line.covered === null ? (
            <>
              <span className="muted" aria-label={t("notAvailable")}>
                —
              </span>
              <span className="cell-sub">
                {instanceRow ? t("notCountedInstance") : t("summaryUnavailable")}
              </span>
            </>
          ) : (
            plainCount(line.covered, i18n.locale)
          )}
        </td>
        <td className="num">
          {line.overrides === null ? (
            <span className="muted" aria-label={t("notAvailable")}>
              —
            </span>
          ) : (
            plainCount(line.overrides, i18n.locale)
          )}
        </td>
        <td className="cell-actions">
          <div className="actions">
            {!instanceRow && (
              <button
                type="button"
                className="action secondary"
                aria-label={line.row ? t("editAria", { name }) : t("setAria", { name })}
                disabled={pending}
                onClick={() => {
                  setRemoving(false);
                  setEditing(!editing);
                }}
              >
                {line.row ? t("edit") : t("set")}
              </button>
            )}
            {line.row && (
              <button
                type="button"
                className="action secondary"
                aria-label={
                  instanceRow ? t("useDefaultInstanceAria", { name }) : t("useDefaultAria", { name })
                }
                disabled={pending}
                onClick={() => {
                  setEditing(false);
                  setOutcome({ kind: "idle" });
                  setRemoving(!removing);
                }}
              >
                {t("useDefault")}
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
                label={line.row ? t("editAria", { name }) : t("setAria", { name })}
                initialMode={line.mode ?? "ALERT"}
                initialGrace={line.grace}
                bucketSeconds={bucketSeconds}
                impactFor={(mode, grace) =>
                  changeFor(line, rows, summary, bucketSeconds, { mode, grace, removal: false }, i18n)
                }
                isUnchanged={(mode, grace) =>
                  line.mode !== null && mode === line.mode && grace === line.grace
                }
                onSave={async (body: PolicyBody) => {
                  await outagePolicyApi.putClass(cls, body);
                  setEditing(false);
                  await onChanged(
                    body.silence_grace_buckets === null
                      ? t("savedDefaultGrace", { name, mode: modeLabel(body.mode, i18n) })
                      : t("savedGrace", {
                          name,
                          mode: modeLabel(body.mode, i18n),
                          grace: formatGrace(body.silence_grace_buckets, bucketSeconds, i18n),
                        }),
                  );
                }}
                onCancel={() => setEditing(false)}
              />
            )}
            {removing && (
              <div className="rule-form outage-form">
                <ImpactNote
                  title={t("beforeDefault")}
                  impact={changeFor(
                    line,
                    rows,
                    summary,
                    bucketSeconds,
                    {
                      mode: instanceRow ? (classRow(rows, cls)?.mode ?? fallback) : fallback,
                      grace: null,
                      removal: true,
                    },
                    i18n,
                  )}
                />
                <div className="actions">
                  <button
                    type="button"
                    className="action"
                    disabled={pending}
                    onClick={() => void remove()}
                  >
                    {pending ? t("saving") : t("useDefault")}
                  </button>
                  <button
                    type="button"
                    className="action secondary"
                    disabled={pending}
                    onClick={() => setRemoving(false)}
                  >
                    {t("keep")}
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
  const t = useTranslations("outage.classes");
  if (lines.length === 0) {
    return (
      <div className="notice" role="status">
        <p>
          <strong>{t("emptyTitle")}</strong>
        </p>
        <p>{t("emptyText")}</p>
      </div>
    );
  }
  return (
    <TableScroll label={t("tableLabel")}>
      <table className="fleet-table outage-table">
        <caption className="sr-only">{t("caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("class")}</th>
            <th scope="col">{t("mode")}</th>
            <th scope="col">{t("grace")}</th>
            <th scope="col" className="num">
              {t("covered")}
            </th>
            <th scope="col" className="num">
              {t("overrides")}
            </th>
            <th scope="col">{t("actions")}</th>
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
