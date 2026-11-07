"use client";

// The policy rows: what each governs, the family and detector, the mode, and who set it and when.
//
// A source's name comes from the API's `source_name`; a bare id is never the name. A row whose
// source or watch has left the inventory says so and offers only removal, because there is nothing
// left to edit. A row that can reach a NO_EVENTS silence says in place how the outage policy
// ranks above it.

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { alertPolicyApi, type AlertPolicyRow } from "@/lib/api";
import { enumLabel, type EnumTranslator } from "@/i18n/enumLabels";
import { detectorGroup, isGone, removeFailure, shortId, touchesOutage } from "@/lib/alertPolicy";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { PolicyEditor } from "./PolicyEditor";

import "@/components/ui/controls.css";

export type Open = { id: string; kind: "edit" | "remove" } | null;

export function modePill(_mode: AlertPolicyRow["mode"]): string {
  // A configured mode is a choice, not a status: all three share the neutral pill.
  return "pill";
}

function PolicyRow({
  row,
  allRows,
  canWrite,
  editing,
  removing,
  setEditing,
  setRemoving,
  onChanged,
  onGone,
}: {
  row: AlertPolicyRow;
  allRows: AlertPolicyRow[];
  canWrite: boolean;
  editing: boolean;
  removing: boolean;
  setEditing: (on: boolean) => void;
  setRemoving: (on: boolean) => void;
  onChanged: (text: string) => Promise<void>;
  onGone: (text: string) => Promise<void>;
}) {
  const t = useTranslations("alertPolicy");
  const enums = useTranslations("enums") as unknown as EnumTranslator;
  const i18n = useBehaviorI18n();
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  const gone = isGone(row);
  const className = (value: string) => enumLabel(enums, "sourceClass", value);
  const isClass = row.scope_kind === "CLASS";
  const watch = row.family === "property_watch";

  const name = isClass
    ? className(row.scope_value)
    : gone
      ? watch
        ? t("table.goneWatch")
        : t("table.goneSource")
      : (row.source_name as string);
  const detector =
    row.detector_type === null
      ? t("table.allDetectors")
      : enumLabel(enums, detectorGroup(row.family), row.detector_type);
  const sub = isClass
    ? t("table.classRow")
    : gone
      ? t("table.goneId", { id: shortId(row.scope_value) })
      : row.source_class
        ? className(row.source_class)
        : "";
  const note = touchesOutage(row);

  async function remove() {
    if (pending) return;
    setPending(true);
    setOutcome({ kind: "pending" });
    try {
      await alertPolicyApi.remove(row.id);
      setRemoving(false);
      await onChanged(t("flash.removed", { name, detector }));
    } catch (err) {
      const failure = removeFailure(err, i18n.t);
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

  return (
    <>
      <tr id={`policy-${row.id}`}>
        <th scope="row" className="cell-source">
          {gone ? <span className="muted">{name}</span> : name}
          {sub && <span className="cell-sub">{sub}</span>}
          <span className="cell-sub">
            {row.instance_id === null ? (
              t("table.allConsoles")
            ) : (
              <span className="pill">
                {row.instance_name
                  ? t("table.console", { name: row.instance_name })
                  : t("table.consoleUnnamed")}
              </span>
            )}
          </span>
        </th>
        <td className="nowrap">{t(`family.${row.family}`)}</td>
        <td>{row.detector_type === null ? <span className="muted">{detector}</span> : detector}</td>
        <td>
          <span className={modePill(row.mode)}>{t(`mode.${row.mode}`)}</span>
        </td>
        <td>
          {row.updated_by ?? <span className="muted">—</span>}
          <UtcTime value={row.updated_at} variant="compact" className="cell-sub nowrap" />
        </td>
        {canWrite && (
          <td className="cell-actions">
            <div className="actions">
              {!gone && (
                <button
                  type="button"
                  className="action secondary"
                  aria-label={t("table.editAria", { name, detector })}
                  disabled={pending}
                  onClick={() => {
                    setRemoving(false);
                    setEditing(!editing);
                  }}
                >
                  {t("table.edit")}
                </button>
              )}
              <button
                type="button"
                className="action secondary"
                aria-label={t("table.removeAria", { name, detector })}
                disabled={pending}
                onClick={() => {
                  setEditing(false);
                  setOutcome({ kind: "idle" });
                  setRemoving(!removing);
                }}
              >
                {t("table.remove")}
              </button>
            </div>
          </td>
        )}
      </tr>
      {note && (
        <tr className="policy-note-row">
          <td colSpan={canWrite ? 6 : 5}>
            {t("table.outageNote")}{" "}
            <Link href="/admin/outage-policy">{t("table.outageLink")}</Link>
          </td>
        </tr>
      )}
      {canWrite && (editing || removing) && (
        <tr className="status-row">
          <td colSpan={6}>
            {editing && (
              <PolicyEditor
                rows={allRows}
                editing={row}
                onSaved={async (text) => {
                  setEditing(false);
                  await onChanged(text);
                }}
                onCancel={() => setEditing(false)}
              />
            )}
            {removing && (
              <div className="rule-form outage-form">
                <div className="impact" role="group" aria-label={t("remove.before")}>
                  <p className="impact-title">{t("remove.before")}</p>
                  <ul className="impact-lines">
                    <li>
                      {t("remove.lines", { name, detector, mode: t(`mode.${row.mode}`) })}
                    </li>
                  </ul>
                </div>
                <div className="actions">
                  <button
                    type="button"
                    className="action"
                    disabled={pending}
                    onClick={() => void remove()}
                  >
                    {pending ? t("remove.removing") : t("remove.confirm")}
                  </button>
                  <button
                    type="button"
                    className="action secondary"
                    disabled={pending}
                    onClick={() => setRemoving(false)}
                  >
                    {t("remove.keep")}
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

export function PolicyTable({
  rows,
  allRows,
  canWrite,
  open,
  setOpen,
  onChanged,
  onGone,
}: {
  rows: AlertPolicyRow[];
  allRows: AlertPolicyRow[];
  canWrite: boolean;
  open: Open;
  setOpen: (v: Open) => void;
  onChanged: (text: string) => Promise<void>;
  onGone: (text: string) => Promise<void>;
}) {
  const t = useTranslations("alertPolicy.table");
  return (
    <TableScroll label={t("label")}>
      <table className="fleet-table outage-table policy-table">
        <caption className="sr-only">{t("caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("governs")}</th>
            <th scope="col">{t("family")}</th>
            <th scope="col">{t("detector")}</th>
            <th scope="col">{t("mode")}</th>
            <th scope="col">{t("changedBy")}</th>
            {canWrite && <th scope="col">{t("actions")}</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <PolicyRow
              key={row.id}
              row={row}
              allRows={allRows}
              canWrite={canWrite}
              editing={open?.id === row.id && open.kind === "edit"}
              removing={open?.id === row.id && open.kind === "remove"}
              setEditing={(on) => setOpen(on ? { id: row.id, kind: "edit" } : null)}
              setRemoving={(on) => setOpen(on ? { id: row.id, kind: "remove" } : null)}
              onChanged={onChanged}
              onGone={onGone}
            />
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
