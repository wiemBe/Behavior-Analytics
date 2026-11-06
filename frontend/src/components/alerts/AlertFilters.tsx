"use client";

// The alert list's filter bar (SOC-7b).
//
// Status and severity are links with the count of alerts under the current filters beside each
// value, so a click is a navigation: the URL is the state, Back and Forward walk the filter
// history, and a link opened in a new tab shows the same view. The search, the family, the log
// source picker and the Custom range live in one form; a change of family or source applies at
// once and carries whatever is typed in the search and the Custom inputs with it.
//
// A count is a measurement, so 0 is printed as 0. When the count read failed there are no counts
// and a note says so; nothing is drawn as zero in their place.

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import type { FormEvent } from "react";

import { useNavigate } from "@/hooks/useNavigate";
import { RangeChips } from "@/components/listview/RangeChips";
import { useEnumLabels } from "@/i18n/enumLabels";
import type { AlertFacets, AlertFacetValue } from "@/lib/api";
import { CLOSE_MARK } from "@/lib/behaviorI18n";
import {
  ALERT_FAMILIES,
  ALERT_SEVERITIES,
  ALERT_STATUSES,
  Q_MAX,
  alertHref,
  isAlertFiltered,
  type AlertQuery,
} from "@/lib/alertQuery";
import { formatCount, type FormatLocale } from "@/lib/format";
import {
  RANGE_CHIPS,
  parseRange,
  type RangeChip,
  type RangeWindow,
} from "@/lib/timeRange";

export interface SourceOption {
  id: string;
  name: string;
}

function countOf(list: AlertFacetValue[] | undefined, value: string): number | null {
  const hit = list?.find((entry) => entry.value === value);
  return hit ? hit.count : null;
}

export function AlertFilters({
  query,
  sources,
  sourcesFailed,
  facets,
  window: shown,
}: {
  query: AlertQuery;
  sources: SourceOption[];
  sourcesFailed: boolean;
  /** The counts under the current filters, or null when the count read failed. */
  facets: AlertFacets | null;
  window: RangeWindow | null;
}) {
  const t = useTranslations("alerts");
  const l = useTranslations("alerts.list");
  const navigate = useNavigate();
  const locale = (useLocale() === "tr" ? "tr" : "en") as FormatLocale;
  const { severityPill } = useEnumLabels();
  const formId = "alert-filters";

  /** What the form holds that the links cannot know: the typed search and the range inputs. */
  const formPatch = (form: HTMLFormElement): Partial<AlertQuery> => {
    const data = new FormData(form);
    const text = (name: string) => {
      const value = data.get(name);
      return typeof value === "string" ? value : "";
    };
    return {
      q: text("q").trim().slice(0, Q_MAX),
      range: parseRange(
        { range: text("range"), since: text("since"), until: text("until") },
        Date.now(),
      ),
    };
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    navigate(alertHref(query, formPatch(event.currentTarget)));
  };

  const chipHrefs = Object.fromEntries(
    RANGE_CHIPS.map(({ key }) => {
      const same = query.range.chip === key;
      const range = same
        ? { chip: "" as const, custom: false, since: "", until: "", issues: [] }
        : parseRange({ range: key }, Date.now());
      return [key, alertHref(query, { range })];
    }),
  ) as Record<RangeChip, string>;

  const count = (list: AlertFacetValue[] | undefined, value: string) => {
    const n = facets ? countOf(list, value) : null;
    return n === null ? null : n;
  };
  const countText = (n: number | null) => (n === null ? null : formatCount(n, locale));

  const filtered = isAlertFiltered(query);
  const sourceName = (id: string) => sources.find((s) => s.id === id)?.name;
  const chips: Array<{ key: string; label: string; href: string }> = [];
  if (query.status) {
    chips.push({
      key: "status",
      label: l("chip.status", { value: t(`filters.status.${query.status}`) }),
      href: alertHref(query, { status: "" }),
    });
  }
  for (const s of query.severity) {
    chips.push({
      key: `severity-${s}`,
      label: l("chip.severity", { value: severityPill(s) }),
      href: alertHref(query, { severity: query.severity.filter((v) => v !== s) }),
    });
  }
  if (query.family) {
    chips.push({
      key: "family",
      label: l("chip.family", { value: l(`family.${query.family}`) }),
      href: alertHref(query, { family: "" }),
    });
  }
  for (const id of query.logSourceIds) {
    const name = sourceName(id);
    chips.push({
      key: `source-${id}`,
      label: name ? l("sourceChip", { name }) : l("sourceChipUnknown", { id: id.slice(0, 8) }),
      href: alertHref(query, { logSourceIds: query.logSourceIds.filter((v) => v !== id) }),
    });
  }
  if (query.q) {
    chips.push({
      key: "q",
      label: l("chip.q", { value: query.q }),
      href: alertHref(query, { q: "" }),
    });
  }

  const pickable = sources.filter((s) => !query.logSourceIds.includes(s.id));

  return (
    <section className="alert-filters" aria-label={l("barLabel")}>
      <form id={formId} className="filters alert-filter-form" onSubmit={onSubmit}>
        <label className="sr-only" htmlFor="alert-q">
          {l("searchLabel")}
        </label>
        <input
          id="alert-q"
          type="search"
          name="q"
          defaultValue={query.q}
          placeholder={l("searchPlaceholder")}
          maxLength={Q_MAX}
          autoComplete="off"
          title={l("searchHelp")}
        />

        <label className="sr-only" htmlFor="alert-status">
          {t("filters.label")}
        </label>
        <select
          id="alert-status"
          name="status"
          value={query.status}
          onChange={(event) =>
            navigate(
              alertHref(query, {
                ...formPatch(event.currentTarget.form!),
                status: event.currentTarget.value as AlertQuery["status"],
              }),
            )
          }
        >
          <option value="">
            {facets ? `${t("filters.all")} (${formatCount(facets.total, locale)})` : t("filters.all")}
          </option>
          {ALERT_STATUSES.map((status) => {
            const n = countText(count(facets?.status, status));
            const name = t(`filters.status.${status}`);
            return (
              <option key={status} value={status}>
                {n === null ? name : `${name} (${n})`}
              </option>
            );
          })}
        </select>

        <label className="filter-field" htmlFor="alert-family">
          {l("familyLabel")}
        </label>
        <select
          id="alert-family"
          value={query.family}
          onChange={(event) =>
            navigate(
              alertHref(query, {
                ...formPatch(event.currentTarget.form!),
                family: event.currentTarget.value as AlertQuery["family"],
              }),
            )
          }
        >
          <option value="">{l("allFamilies")}</option>
          {ALERT_FAMILIES.map((family) => {
            const n = countText(count(facets?.family, family));
            return (
              <option key={family} value={family}>
                {n === null ? l(`family.${family}`) : `${l(`family.${family}`)} (${n})`}
              </option>
            );
          })}
        </select>

        {!sourcesFailed && (
          <>
            <label className="sr-only" htmlFor="alert-source">
              {l("sourceLabel")}
            </label>
            <select
              id="alert-source"
              value=""
              onChange={(event) => {
                const id = event.currentTarget.value;
                if (!id) return;
                navigate(
                  alertHref(query, {
                    ...formPatch(event.currentTarget.form!),
                    logSourceIds: [...query.logSourceIds, id],
                  }),
                );
              }}
            >
              <option value="">{l("addSource")}</option>
              {pickable.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </>
        )}

        <button type="submit">{l("apply")}</button>
      </form>

      <div className="facet-row">
        <div className="facet-group" role="group" aria-label={l("severityGroup")}>
          {ALERT_SEVERITIES.map((s) => {
            const on = query.severity.includes(s);
            const n = count(facets?.severity, s);
            const name = severityPill(s);
            return (
              <Link
                key={s}
                href={alertHref(query, {
                  severity: on ? query.severity.filter((v) => v !== s) : [...query.severity, s],
                })}
                className="facet"
                aria-current={on ? "true" : undefined}
                aria-label={n === null ? name : `${name}: ${l("facetCount", { count: n })}`}
              >
                {name}
                {n !== null && <span className="facet-count">{formatCount(n, locale)}</span>}
              </Link>
            );
          })}
        </div>
      </div>

      <RangeChips
        formId={formId}
        chipHrefs={chipHrefs}
        active={query.range.chip}
        custom={query.range.custom}
        since={query.range.custom ? query.range.since : ""}
        until={query.range.custom ? query.range.until : ""}
        window={shown}
        emptyText={t("list.allTime")}
      />

      {!facets && (
        <p className="banner warn" role="status">
          {l("facetsFailed")}
        </p>
      )}
      {sourcesFailed && (
        <p className="banner warn" role="status">
          {l("sourcesFailed")}
        </p>
      )}

      {filtered && chips.length > 0 && (
        <div className="row filter-chips">
          <span className="muted">{l("filteredBy")}</span>
          {chips.map((chip) => (
            <Link
              key={chip.key}
              href={chip.href}
              className="filter-chip"
              aria-label={l("removeFilter", { chip: chip.label })}
            >
              {chip.label}
              <span aria-hidden="true">{CLOSE_MARK}</span>
            </Link>
          ))}
          <Link href="/alerts" className="filter-clear">
            {l("clearAll")}
          </Link>
        </div>
      )}
    </section>
  );
}
