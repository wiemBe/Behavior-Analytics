"use client";

// The controls above the source list: a search box, the facet filters with their counts, the
// removable chips, the page size and the CSV link. Nothing here holds the filter state; the URL
// does. A control builds the next query and hands it to `go`, which navigates.

import { useTranslations } from "next-intl";

import { asTextTranslator } from "@/i18n/textTranslator";
import { useEffect, useId, useRef, useState } from "react";

import { fleetApi, fleetSourcesCsvUrl, type FleetSourceFacets, type SourceClass, type YesNo } from "@/lib/api";
import { basisLabel, classLabel, reviewLabel } from "@/lib/fleet";
import { useFleetText } from "@/lib/fleetText";
import {
  CRITICALITIES,
  EPS_STATES,
  OPTIONAL_COLUMNS,
  YES_NO,
  type OptionalColumn,
  EXPORT_CAP,
  FLOWS,
  PAGE_SIZES,
  PROFILE_KINDS,
  Q_MAX,
  DISABLED_FLOW,
  ENABLED_FLOWS,
  qradarStatusOf,
  withQradarStatus,
  changed,
  clearedFilters,
  filterCount,
  groupName,
  toApiParams,
  toggled,
  typeName,
  type SourceListQuery,
} from "@/lib/fleetSources";

import "./fleet-sources.css";

const SEARCH_DEBOUNCE_MS = 250;
const REMOVE_MARK = "×";

interface Option {
  value: string;
  label: string;
  count?: number;
}

function Facet({
  name,
  options,
  selected,
  onToggle,
  searchLabel,
  emptyText,
  remoteFind,
}: {
  name: string;
  options: Option[];
  selected: string[];
  onToggle: (value: string) => void;
  /** Present for a long list: a box that narrows the values in this facet only. */
  searchLabel?: string;
  emptyText: string;
  /** The search is answered by the API: `options` already is the narrowed list. */
  remoteFind?: { value: string; set: (value: string) => void };
}) {
  const t = useTranslations("fleet.sourceList.facets");
  const [localFind, setLocalFind] = useState("");
  const find = remoteFind ? remoteFind.value : localFind;
  const setFind = remoteFind ? remoteFind.set : setLocalFind;
  const id = useId();
  const needle = find.trim().toLocaleLowerCase("en-US");
  const shown = needle && !remoteFind
    ? options.filter((o) => o.label.toLocaleLowerCase("en-US").includes(needle))
    : options;
  return (
    <details className="fs-facet">
      <summary>
        <span>{name}</span>
        {selected.length > 0 && (
          <span className="fs-facet-n" title={t("selected", { count: selected.length })}>
            {selected.length}
          </span>
        )}
      </summary>
      <div className="fs-facet-panel" role="group" aria-label={name}>
        {searchLabel && (
          <>
            <label className="sr-only" htmlFor={`${id}-find`}>
              {searchLabel}
            </label>
            <input
              id={`${id}-find`}
              type="search"
              value={find}
              placeholder={searchLabel}
              onChange={(e) => setFind(e.target.value)}
            />
          </>
        )}
        <ul className="fs-options">
          {shown.map((o) => (
            <li key={o.value}>
              <label>
                <input
                  type="checkbox"
                  checked={selected.includes(o.value)}
                  onChange={() => onToggle(o.value)}
                />
                <span className="fs-option-label">{o.label}</span>
                {o.count !== undefined && <span className="num fs-option-count">{o.count.toLocaleString("en-US")}</span>}
              </label>
            </li>
          ))}
        </ul>
        {shown.length === 0 && <p className="muted fs-facet-empty">{emptyText}</p>}
      </div>
    </details>
  );
}

const GROUP_SEARCH_MS = 250;

/**
 * The group facet. Its search asks the API (`group_q`) so one group is findable among hundreds;
 * the list itself is not changed by it.
 */
function useRemoteFind(
  query: SourceListQuery,
  listed: Option[],
  param: "group_q" | "owner_q",
): [string, (v: string) => void, Option[]] {
  const [find, setFind] = useState("");
  const [found, setFound] = useState<Option[] | null>(null);
  const needle = find.trim();
  useEffect(() => {
    if (!needle) {
      setFound(null);
      return;
    }
    let live = true;
    const timer = setTimeout(() => {
      fleetApi
        .sourceList({ ...toApiParams(query), [param]: needle, limit: 1, offset: 0 })
        .then((p) => {
          if (!live) return;
          setFound(
            param === "group_q"
              ? p.facets.group.map((g) => ({ value: String(g.group_id), label: g.name, count: g.count }))
              : p.facets.owner.map((o) => ({ value: o.value, label: o.value, count: o.count })),
          );
        })
        .catch(() => {
          if (live) setFound([]);
        });
    }, GROUP_SEARCH_MS);
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needle, toSearchKey(query)]);
  return [find, setFind, needle ? (found ?? listed) : listed];
}

function toSearchKey(q: SourceListQuery): string {
  return JSON.stringify(toApiParams(q));
}

/** Selected values the facet no longer lists (count 0 under the other filters) stay visible. */
function withSelected(listed: Option[], selected: string[], labelOf: (v: string) => string): Option[] {
  const have = new Set(listed.map((o) => o.value));
  const missing = selected.filter((v) => !have.has(v)).map((v) => ({ value: v, label: labelOf(v), count: 0 }));
  return [...listed, ...missing];
}

export function FleetSourceControls({
  query,
  facets,
  total,
  go,
}: {
  query: SourceListQuery;
  facets: FleetSourceFacets | undefined;
  total: number;
  go: (next: SourceListQuery, mode?: "push" | "replace") => void;
}) {
  const tt = useTranslations("fleet.sourceList");
  const t = asTextTranslator(tt);
  const text = useFleetText();
  const [q, setQ] = useState(query.q);
  const root = useRef<HTMLDivElement>(null);
  const searchId = useId();

  // The URL is the truth: a chip, "Clear all" or the back button rewrites the box.
  useEffect(() => setQ(query.q), [query.q]);

  // Debounced search; the typed text replaces the history entry rather than adding one per key.
  useEffect(() => {
    const next = q.trim();
    if (next === query.q) return;
    const timer = setTimeout(() => go(changed(query, { q: next }), "replace"), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  // An open facet closes on a click elsewhere and on Escape.
  useEffect(() => {
    const closeAll = (except?: Element | null) =>
      root.current?.querySelectorAll("details.fs-facet[open]").forEach((d) => {
        if (d !== except) d.removeAttribute("open");
      });
    const onPointer = (e: Event) => closeAll((e.target as Element | null)?.closest("details.fs-facet"));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeAll();
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  const classOptions = withSelected(
    (facets?.source_class ?? []).map((f) => ({ value: f.value, label: classLabel(f.value, text), count: f.count })),
    query.class,
    (v) => classLabel(v, text),
  );
  const typeOptions = withSelected(
    (facets?.type ?? [])
      .filter((f) => f.type_id !== null)
      .map((f) => ({ value: String(f.type_id), label: f.name ?? `#${f.type_id}`, count: f.count })),
    query.type_id.map(String),
    (v) => typeName(facets, Number(v)),
  );
  const status = qradarStatusOf(query.flow);
  const flowCount = (v: (typeof FLOWS)[number]) => facets?.flow.find((f) => f.value === v)?.count ?? 0;
  const statusCounts = {
    ENABLED: ENABLED_FLOWS.reduce((n, v) => n + flowCount(v), 0),
    DISABLED: flowCount(DISABLED_FLOW),
  };
  // A status filter is shown in the status control, not twice in the flow facet.
  const flowSelected = status === null ? query.flow : [];
  const groupListed = withSelected(
    (facets?.group ?? []).map((g) => ({ value: String(g.group_id), label: g.name, count: g.count })),
    query.group.map(String),
    (v) => groupName(facets, Number(v)),
  );
  const critOptions = withSelected(
    CRITICALITIES.map((v) => ({
      value: v,
      label: t(`criticality.${v}`),
      count: facets?.criticality.find((f) => f.value === v)?.count,
    })),
    query.criticality,
    (v) => t(`criticality.${v}`),
  );
  const ownerListed = withSelected(
    (facets?.owner ?? []).map((o) => ({ value: o.value, label: o.value, count: o.count })),
    query.owner,
    (v) => v,
  );
  const [ownerFind, setOwnerFind, ownerOptions] = useRemoteFind(query, ownerListed, "owner_q");
  const collectorOptions = withSelected(
    (facets?.collector ?? []).map((c) => ({ value: c.value, label: `#${c.value}`, count: c.count })),
    query.collector.map(String),
    (v) => `#${v}`,
  );
  const yesNoOptions = (family: "auto_discovered" | "monitoring" | "maintenance") =>
    YES_NO.map((v) => ({
      value: v,
      label: t(`yesNo.${v}`),
      count: facets?.[family].find((f) => f.value === v)?.count,
    }));
  const [groupFind, setGroupFind, groupOptions] = useRemoteFind(query, groupListed, "group_q");
  const flowOptions = withSelected(
    FLOWS.filter((v) => v !== DISABLED_FLOW && (facets?.flow.some((f) => f.value === v) ?? false)).map((v) => ({
      value: v,
      label: t(`flow.${v}`),
      count: flowCount(v),
    })),
    flowSelected,
    (v) => t(`flow.${v}`),
  );
  const epsOptions = EPS_STATES.map((v) => ({ value: v, label: t(`epsState.${v}`) }));
  const profileOptions = PROFILE_KINDS.map((v) => ({
    value: v,
    label: t(`profile.${v}`),
    count: facets?.profile.find((f) => f.value === v)?.count,
  }));

  const chips: { key: string; label: string; next: SourceListQuery }[] = [];
  if (query.q) chips.push({ key: "q", label: t("chips.q", { value: query.q }), next: changed(query, { q: "" }) });
  for (const v of query.class)
    chips.push({ key: `class-${v}`, label: t("chips.class", { value: classLabel(v, text) }), next: changed(query, { class: toggled(query.class, v) }) });
  for (const v of query.type_id)
    chips.push({ key: `type-${v}`, label: t("chips.type", { value: typeName(facets, v) }), next: changed(query, { type_id: toggled(query.type_id, v) }) });
  if (status)
    chips.push({ key: "status", label: t("chips.status", { value: t(`status.${status}`) }), next: changed(query, { flow: [] }) });
  for (const v of query.group)
    chips.push({ key: `group-${v}`, label: t("chips.group", { value: groupName(facets, v) }), next: changed(query, { group: toggled(query.group, v) }) });
  for (const v of query.criticality)
    chips.push({ key: `crit-${v}`, label: t("chips.criticality", { value: t(`criticality.${v}`) }), next: changed(query, { criticality: toggled(query.criticality, v) }) });
  for (const v of query.owner)
    chips.push({ key: `owner-${v}`, label: t("chips.owner", { value: v }), next: changed(query, { owner: toggled(query.owner, v) }) });
  for (const v of query.collector)
    chips.push({ key: `coll-${v}`, label: t("chips.collector", { value: `#${v}` }), next: changed(query, { collector: toggled(query.collector, v) }) });
  for (const family of ["auto_discovered", "monitoring", "maintenance"] as const)
    for (const v of query[family])
      chips.push({ key: `${family}-${v}`, label: t(`chips.${family}`, { value: t(`yesNo.${v}`) }), next: changed(query, { [family]: toggled(query[family], v) }) });
  for (const v of flowSelected)
    chips.push({ key: `flow-${v}`, label: t("chips.flow", { value: t(`flow.${v}`) }), next: changed(query, { flow: toggled(query.flow, v) }) });
  for (const v of query.eps)
    chips.push({ key: `eps-${v}`, label: t("chips.eps", { value: t(`epsState.${v}`) }), next: changed(query, { eps: toggled(query.eps, v) }) });
  for (const v of query.profile)
    chips.push({ key: `profile-${v}`, label: t("chips.profile", { value: t(`profile.${v}`) }), next: changed(query, { profile: toggled(query.profile, v) }) });
  if (query.review_status)
    chips.push({ key: "review", label: t("chips.review", { value: reviewLabel(query.review_status, text) }), next: changed(query, { review_status: "" }) });
  if (query.has_conflict)
    chips.push({ key: "conflict", label: query.has_conflict === "true" ? t("chips.conflictYes") : t("chips.conflictNo"), next: changed(query, { has_conflict: "" }) });
  if (query.basis)
    chips.push({ key: "basis", label: t("chips.basis", { value: basisLabel(query.basis, text) }), next: changed(query, { basis: "" }) });

  const csv = fleetSourcesCsvUrl(toApiParams(query));

  return (
    <div className="fs-controls" ref={root}>
      <div className="fs-bar">
        <div className="fs-search">
          <label className="sr-only" htmlFor={searchId}>
            {t("search.label")}
          </label>
          <input
            id={searchId}
            type="search"
            value={q}
            maxLength={Q_MAX}
            placeholder={t("search.placeholder")}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="fs-status" role="group" aria-label={t("status.label")}>
          {(["ENABLED", "DISABLED"] as const).map((v) => (
            <button
              key={v}
              type="button"
              className="fs-status-btn"
              aria-pressed={status === v}
              onClick={() => go(changed(query, { flow: status === v ? [] : withQradarStatus([], v) }))}
            >
              {t(`status.${v}`)}
              {facets && <span className="num fs-option-count">{statusCounts[v].toLocaleString("en-US")}</span>}
            </button>
          ))}
        </div>
        <div className="fs-facets" role="group" aria-label={t("facets.group")}>
          <Facet
            name={t("facets.class")}
            options={classOptions}
            selected={query.class}
            emptyText={t("facets.noOptions")}
            onToggle={(v) => go(changed(query, { class: toggled(query.class, v as SourceClass) }))}
          />
          <Facet
            name={t("facets.type")}
            options={typeOptions}
            selected={query.type_id.map(String)}
            searchLabel={t("facets.typeSearch")}
            emptyText={t("facets.noType")}
            onToggle={(v) => go(changed(query, { type_id: toggled(query.type_id, Number(v)) }))}
          />
          <Facet
            name={t("facets.byGroup")}
            options={groupOptions}
            selected={query.group.map(String)}
            searchLabel={t("facets.groupSearch")}
            remoteFind={{ value: groupFind, set: setGroupFind }}
            emptyText={t("facets.noGroup")}
            onToggle={(v) => go(changed(query, { group: toggled(query.group, Number(v)) }))}
          />
          <Facet
            name={t("facets.criticality")}
            options={critOptions}
            selected={query.criticality}
            emptyText={t("facets.noOptions")}
            onToggle={(v) => go(changed(query, { criticality: toggled(query.criticality, v) }))}
          />
          <Facet
            name={t("facets.owner")}
            options={ownerOptions}
            selected={query.owner}
            searchLabel={t("facets.ownerSearch")}
            remoteFind={{ value: ownerFind, set: setOwnerFind }}
            emptyText={t("facets.noOwner")}
            onToggle={(v) => go(changed(query, { owner: toggled(query.owner, v) }))}
          />
          <Facet
            name={t("facets.collector")}
            options={collectorOptions}
            selected={query.collector.map(String)}
            emptyText={t("facets.noOptions")}
            onToggle={(v) => go(changed(query, { collector: toggled(query.collector, Number(v)) }))}
          />
          <Facet
            name={t("facets.autoDiscovered")}
            options={yesNoOptions("auto_discovered")}
            selected={query.auto_discovered}
            emptyText={t("facets.noOptions")}
            onToggle={(v) => go(changed(query, { auto_discovered: toggled(query.auto_discovered, v as YesNo) }))}
          />
          <Facet
            name={t("facets.monitoring")}
            options={yesNoOptions("monitoring")}
            selected={query.monitoring}
            emptyText={t("facets.noOptions")}
            onToggle={(v) => go(changed(query, { monitoring: toggled(query.monitoring, v as YesNo) }))}
          />
          <Facet
            name={t("facets.maintenance")}
            options={yesNoOptions("maintenance")}
            selected={query.maintenance}
            emptyText={t("facets.noOptions")}
            onToggle={(v) => go(changed(query, { maintenance: toggled(query.maintenance, v as YesNo) }))}
          />
          <Facet
            name={t("facets.flow")}
            options={flowOptions}
            selected={flowSelected}
            emptyText={t("facets.noOptions")}
            onToggle={(v) =>
              go(changed(query, { flow: toggled(flowSelected, v as (typeof FLOWS)[number]) }))
            }
          />
          <Facet
            name={t("facets.eps")}
            options={epsOptions}
            selected={query.eps}
            emptyText={t("facets.noOptions")}
            onToggle={(v) => go(changed(query, { eps: toggled(query.eps, v as (typeof EPS_STATES)[number]) }))}
          />
          <Facet
            name={t("facets.profile")}
            options={profileOptions}
            selected={query.profile}
            emptyText={t("facets.noOptions")}
            onToggle={(v) => go(changed(query, { profile: toggled(query.profile, v as (typeof PROFILE_KINDS)[number]) }))}
          />
        </div>
        <Facet
          name={t("columns.button")}
          options={OPTIONAL_COLUMNS.map((c) => ({ value: c, label: t(`columns.${c}`) }))}
          selected={query.cols}
          emptyText={t("facets.noOptions")}
          onToggle={(v) => {
            const on = toggled(query.cols, v as OptionalColumn);
            go({ ...query, cols: OPTIONAL_COLUMNS.filter((c) => on.includes(c)) }, "replace");
          }}
        />
        <label className="fs-perpage">
          <span className="muted">{t("pager.perPage")}</span>
          <select
            value={query.limit}
            onChange={(e) => go(changed(query, { limit: Number(e.target.value) }))}
          >
            {PAGE_SIZES.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <a className="action secondary fs-export" href={csv}>
          {t("export.button")}
        </a>
      </div>
      {total > EXPORT_CAP && (
        <p className="muted fs-export-note">
          {t("export.capped", { cap: EXPORT_CAP.toLocaleString("en-US"), total: total.toLocaleString("en-US") })}
        </p>
      )}
      {filterCount(query) > 0 && (
        <ul className="filter-chips fleet-filter-chips fs-chips" aria-label={t("chips.label")}>
          {chips.map((c) => (
            <li key={c.key} className="filter-chip">
              <span>{c.label}</span>
              <button
                type="button"
                className="fs-chip-x"
                aria-label={t("chips.remove", { label: c.label })}
                onClick={() => go(c.next)}
              >
                {REMOVE_MARK}
              </button>
            </li>
          ))}
          <li>
            <button type="button" className="fs-clear" onClick={() => go(clearedFilters(query))}>
              {t("chips.clearAll")}
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}
