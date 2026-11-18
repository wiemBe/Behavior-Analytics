"use client";

// The source table with its row selection and the bulk class bar.
//
// A bulk change is one request for the selected rows of this page (at most the page size, and the
// API takes 500). It names the count before it runs, and every answer the API can give is said:
// 403 with the permission, 404 with the number of sources that no longer exist, 422 with its
// reason. Permissions are never judged here beyond hiding the controls; the API refuses.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { type TextTranslator } from "@/i18n/textTranslator";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { useSession } from "@/components/auth/session";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import {
  ApiError,
  fleetApi,
  type FleetProfileCell,
  type FleetSourceRow,
  type FleetSourceSort,
  type SourceClass,
} from "@/lib/api";
import { ASSIGNABLE_CLASSES, basisLabel, classLabel, classTone, formatEpsValue } from "@/lib/fleet";
import { useFleetText } from "@/lib/fleetText";
import { formatAge } from "@/lib/health";
import { WRITE_PERMISSION, holds, sortedBy, type SourceListQuery } from "@/lib/fleetSources";

import "@/components/ui/controls.css";
import "./fleet-sources.css";

const SORTABLE: Record<string, FleetSourceSort> = {
  name: "name",
  type: "type",
  class: "class",
  flow: "silent_for",
  lastEvent: "last_event",
  eps: "eps",
};

function ProfileBadge({ kind, cell }: { kind: string; cell: FleetProfileCell | null }) {
  const t = useTranslations("fleet.sourceList");
  if (cell === null) return null;
  const status = t(`profileStatus.${cell.status}`);
  if (!cell.enabled) {
    return (
      <li>
        <span
          className="pill pill-quiet"
          title={t("cell.profileOffTitle", { kind, status })}
        >
          {t("cell.profileOff", { kind })}
        </span>
      </li>
    );
  }
  const tone =
    cell.status === "VALID" ? "ok" : cell.status === "INVALID" ? "crit" : cell.status === "INCONCLUSIVE" ? "warn" : "";
  return (
    <li>
      <span className={`pill pill-quiet ${tone}`}>{t("cell.profileOn", { kind, status })}</span>
    </li>
  );
}

function EpsCell({ row }: { row: FleetSourceRow }) {
  const t = useTranslations("fleet.sourceList");
  const text = useFleetText();
  const current = formatEpsValue(row.current_eps, text.locale);
  if (current === null)
    return (
      <span className="muted" title={t("cell.notMeasured")}>
        —
      </span>
    );
  const low = formatEpsValue(row.expected_eps_low, text.locale);
  const high = formatEpsValue(row.expected_eps_high, text.locale);
  const point = formatEpsValue(row.expected_eps, text.locale);
  const band =
    low !== null && high !== null
      ? t("cell.expectedBand", { low, high })
      : point !== null
        ? t("cell.expectedPoint", { value: point })
        : null;
  const tone = row.eps_state === "BELOW_EXPECTED" ? "crit" : row.eps_state === "ABOVE_EXPECTED" ? "warn" : "";
  return (
    <>
      <span className={tone ? `fs-eps-${tone}` : undefined}>
        {current}
        {row.eps_state && row.eps_state !== "WITHIN_EXPECTED" && (
          <span className="sr-only"> {t(`epsState.${row.eps_state}`)}</span>
        )}
      </span>
      {row.eps_state === "NO_BASELINE" || band === null ? (
        <span className="cell-sub" title={t("cell.noBaseline")}>
          —
        </span>
      ) : (
        <span className="cell-sub">
          {band}
        </span>
      )}
    </>
  );
}

function ageOf(iso: string | null, nowIso: string): string | undefined {
  if (!iso) return undefined;
  const seconds = (Date.parse(nowIso) - Date.parse(iso)) / 1000;
  return Number.isFinite(seconds) && seconds >= 0 ? formatAge(Math.round(seconds)) : undefined;
}

function Row({
  row,
  nowIso,
  selectable,
  checked,
  onCheck,
}: {
  row: FleetSourceRow;
  nowIso: string;
  selectable: boolean;
  checked: boolean;
  onCheck: (checked: boolean) => void;
}) {
  const t = useTranslations("fleet.sourceList");
  const text = useFleetText();
  const age = ageOf(row.last_event_at, nowIso);
  const more = row.group_count - row.group_names.length;
  const profiles = [
    ["WAF", row.waf_profile],
    ["IPS", row.ips_profile],
    ["Identity", row.identity_profile],
  ] as const;
  const hasProfile = profiles.some(([, c]) => c !== null);
  return (
    <tr aria-selected={selectable ? checked : undefined}>
      {selectable && (
        <td className="fs-check">
          <input
            type="checkbox"
            checked={checked}
            aria-label={t("table.selectRow", { name: row.name })}
            onChange={(e) => onCheck(e.target.checked)}
          />
        </td>
      )}
      <th scope="row" className="cell-source">
        <Link href={`/log-sources/${row.id}`}>{row.name}</Link>
        {!row.enabled && <span className="pill pill-quiet fs-disabled">{t("cell.disabled")}</span>}
      </th>
      <td className="fs-type" title={row.type_name ?? undefined}>
        {row.type_name ?? (
          <span className="muted" title={t("cell.notRecorded")}>
            —
          </span>
        )}
      </td>
      <td className="fs-ident">
        {row.identifier ?? (
          <span className="muted" title={t("cell.notRecorded")}>
            —
          </span>
        )}
      </td>
      <td className="num">{row.qradar_id}</td>
      <td>
        <span
          className={`pill ${classTone(row.source_class)}`}
          title={row.override_class !== null ? undefined : basisLabel(row.basis, text)}
        >
          {classLabel(row.source_class, text)}
        </span>
        {row.override_class !== null && (
          <span className="cell-sub">
            {row.override_by ? t("cell.override", { name: row.override_by }) : t("cell.overrideAnon")}
          </span>
        )}
      </td>
      <td>
        {t(`flow.${row.flow}`)}
        {row.flow === "SILENT" && row.silent_for_seconds != null && (
          <span className="cell-sub">{t("cell.silentFor", { age: formatAge(row.silent_for_seconds) })}</span>
        )}
      </td>
      <td className="num">
        <EpsCell row={row} />
      </td>
      <td>
        {hasProfile ? (
          <ul className="plain-list fs-profiles">
            {profiles.map(([kind, cell]) => (
              <ProfileBadge key={kind} kind={kind} cell={cell} />
            ))}
          </ul>
        ) : (
          <span className="muted" title={t("cell.noProfiles")}>
            —
          </span>
        )}
      </td>
      <td className="num cell-when">
        {row.last_event_at ? (
          <UtcTime value={row.last_event_at} variant="compact" age={age ? t("cell.ago", { age }) : null} wrapZone />
        ) : (
          <span className="muted" title={t("cell.noEvent")}>
            —
          </span>
        )}
      </td>
      <td className="fs-groups">
        {row.group_names.length === 0 ? (
          <span className="muted">—</span>
        ) : (
          <>
            {row.group_names.join(", ")}
            {more > 0 && <span className="muted"> {t("cell.moreGroups", { count: more })}</span>}
          </>
        )}
      </td>
    </tr>
  );
}

type Phase =
  | { kind: "idle" }
  | { kind: "confirm" }
  | { kind: "applying" }
  | { kind: "error"; message: string };

function BulkBar({
  selected,
  onClear,
  onDone,
}: {
  selected: string[];
  onClear: () => void;
  onDone: (updated: number) => void;
}) {
  const t = useTranslations("fleet.sourceList.bulk");
  const text = useFleetText();
  const [choice, setChoice] = useState<SourceClass | "CLEAR" | "">("");
  const [reviewed, setReviewed] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const selectId = useId();
  const count = selected.length;

  async function apply() {
    if (choice === "" || phase.kind === "applying") return;
    setPhase({ kind: "applying" });
    try {
      const res = await fleetApi.bulkClassification({
        log_source_ids: selected,
        class: choice === "CLEAR" ? null : choice,
        mark_reviewed: reviewed,
      });
      onDone(res.updated);
    } catch (err) {
      setPhase({ kind: "error", message: bulkFailure(err, text.say) });
    }
  }

  const className = choice === "CLEAR" || choice === "" ? "" : classLabel(choice, text);
  return (
    <section className="fs-bulk" aria-label={t("label")}>
      <p className="fs-bulk-count" role="status">
        {t("selected", { count })}
      </p>
      <div className="fs-bulk-controls">
        <label htmlFor={selectId} className="sr-only">
          {t("classLabel")}
        </label>
        <select
          id={selectId}
          value={choice}
          disabled={phase.kind === "applying"}
          onChange={(e) => {
            setChoice(e.target.value as SourceClass | "CLEAR" | "");
            setPhase({ kind: "idle" });
          }}
        >
          <option value="">{t("choose")}</option>
          {ASSIGNABLE_CLASSES.map((c) => (
            <option key={c} value={c}>
              {classLabel(c, text)}
            </option>
          ))}
          <option value="CLEAR">{t("clearOverride")}</option>
        </select>
        <label className="filter-field filter-check">
          <input
            type="checkbox"
            checked={reviewed}
            disabled={phase.kind === "applying"}
            onChange={(e) => setReviewed(e.target.checked)}
          />
          {t("markReviewed")}
        </label>
        <button
          type="button"
          className="action"
          disabled={choice === "" || count === 0 || phase.kind === "applying" || phase.kind === "confirm"}
          onClick={() => setPhase({ kind: "confirm" })}
        >
          {t("review")}
        </button>
        <button type="button" className="action secondary" onClick={onClear} disabled={phase.kind === "applying"}>
          {t("clearSelection")}
        </button>
      </div>
      {phase.kind === "confirm" && (
        <div className="fs-confirm" role="alertdialog" aria-label={t("confirm")}>
          <p>
            {choice === "CLEAR"
              ? t("confirmClear", { count })
              : t("confirmSet", { count, class: className })}
            {reviewed ? ` ${t("confirmReviewed")}` : ""}
          </p>
          <div className="actions">
            <button type="button" className="action" onClick={() => void apply()}>
              {t("confirm")}
            </button>
            <button type="button" className="action secondary" onClick={() => setPhase({ kind: "idle" })}>
              {t("cancel")}
            </button>
          </div>
        </div>
      )}
      {phase.kind === "applying" && (
        <p className="change-status" role="status">
          {t("applying")}
        </p>
      )}
      {phase.kind === "error" && (
        <p className="change-status error" role="alert">
          {phase.message}
        </p>
      )}
    </section>
  );
}

function bulkFailure(err: unknown, say: TextTranslator): string {
  const k = (key: string) => `fleet.sourceList.bulk.${key}`;
  if (err instanceof ApiError) {
    if (err.status === 403) return say(k("forbidden"), { permission: WRITE_PERMISSION });
    if (err.status === 404) {
      const n = /^(\d+) log sources? not found/.exec(err.message)?.[1];
      return say(k("notFound"), { count: n ?? "some" });
    }
    if (err.status === 422) return say(k("refused"), { detail: err.message });
    return say(k("failed"), { detail: err.message });
  }
  return say(k("failed"), { detail: err instanceof Error ? err.message : String(err) });
}

export function FleetSourceList({
  rows,
  query,
  nowIso,
  go,
  onChanged,
}: {
  rows: FleetSourceRow[];
  query: SourceListQuery;
  nowIso: string;
  go: (next: SourceListQuery, mode?: "push" | "replace") => void;
  /** After a bulk change: reload what the server rendered. */
  onChanged: () => void;
}) {
  const t = useTranslations("fleet.sourceList");
  const { me, status } = useSession();
  const canWrite = holds(me, WRITE_PERMISSION);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [done, setDone] = useState<number | null>(null);
  const allRef = useRef<HTMLInputElement>(null);

  // A new page of rows is a new selection: ids of rows that are gone are not carried along.
  const ids = useMemo(() => rows.map((r) => r.id), [rows]);
  useEffect(() => {
    setSelected((prev) => {
      const keep = new Set([...prev].filter((id) => ids.includes(id)));
      return keep.size === prev.size ? prev : keep;
    });
  }, [ids]);

  const picked = ids.filter((id) => selected.has(id));
  const allChecked = rows.length > 0 && picked.length === rows.length;
  useEffect(() => {
    if (allRef.current) allRef.current.indeterminate = picked.length > 0 && !allChecked;
  }, [picked.length, allChecked]);

  const head = (key: keyof typeof SORTABLE, label: string, num = false) => {
    const column = SORTABLE[key];
    const active = query.sort === column;
    return (
      <th
        scope="col"
        className={num ? "num" : undefined}
        aria-sort={active ? (query.order === "asc" ? "ascending" : "descending") : undefined}
      >
        <button type="button" className="fs-sort" onClick={() => go(sortedBy(query, column))}>
          {label}
          <span aria-hidden="true" className="fs-sort-mark">
            {active ? (query.order === "asc" ? "▲" : "▼") : ""}
          </span>
        </button>
      </th>
    );
  };

  return (
    <>
      {!canWrite && status === "ready" && (
        <p className="muted fs-readonly">{t("bulk.needsPermission", { permission: WRITE_PERMISSION })}</p>
      )}
      {canWrite && picked.length > 0 && (
        <BulkBar
          selected={picked}
          onClear={() => setSelected(new Set())}
          onDone={(updated) => {
            setSelected(new Set());
            setDone(updated);
            onChanged();
          }}
        />
      )}
      {done !== null && picked.length === 0 && (
        <p className="change-status ok" role="status">
          {t("bulk.done", { count: done })}
        </p>
      )}
      <TableScroll label={t("table.label")}>
        <table className="fleet-table fs-table">
          <caption className="sr-only">{t("table.caption")}</caption>
          <thead>
            <tr>
              {canWrite && (
                <th scope="col" className="fs-check">
                  <input
                    ref={allRef}
                    type="checkbox"
                    checked={allChecked}
                    aria-label={t("table.selectAll", { count: rows.length })}
                    onChange={(e) => setSelected(e.target.checked ? new Set(ids) : new Set())}
                  />
                </th>
              )}
              {head("name", t("table.name"))}
              {head("type", t("table.type"))}
              <th scope="col">{t("table.identifier")}</th>
              <th scope="col" className="num">
                {t("table.qradarId")}
              </th>
              {head("class", t("table.class"))}
              {head("flow", t("table.flow"))}
              {head("eps", t("table.eps"), true)}
              <th scope="col">{t("table.profiles")}</th>
              {head("lastEvent", t("table.lastEvent"), true)}
              <th scope="col">{t("table.groups")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <Row
                key={r.id}
                row={r}
                nowIso={nowIso}
                selectable={canWrite}
                checked={selected.has(r.id)}
                onCheck={(on) =>
                  setSelected((prev) => {
                    const next = new Set(prev);
                    if (on) next.add(r.id);
                    else next.delete(r.id);
                    return next;
                  })
                }
              />
            ))}
          </tbody>
        </table>
      </TableScroll>
    </>
  );
}
