// The controls of one IPS source page: range, series and entity dimension. Plain links, so every
// choice is in the URL and the view is shareable; the active option carries `aria-current`. A
// link keeps the other choices and drops the page offset, because a page of one list is not a page
// of another.

import { useTranslations } from "next-intl";
import Link from "next/link";

import type { IpsDimensionName } from "@/lib/api";
import { wordOrValue } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { SOURCE_SERIES_KEYS, type SourceSeriesKey } from "./ipsSourceSeries";

export const IPS_RANGES = [
  { key: "24h", hours: 24 },
  { key: "7d", hours: 24 * 7 },
] as const;
export type IpsRangeKey = (typeof IPS_RANGES)[number]["key"];

export function parseIpsRange(value: string | undefined): (typeof IPS_RANGES)[number] {
  return IPS_RANGES.find((r) => r.key === value) ?? IPS_RANGES[0];
}

/** The dimensions that have entities. The API accepts more names; these are the three it admits. */
export const ENTITY_DIMENSIONS: readonly IpsDimensionName[] = [
  "DESTINATION_PORT",
  "DESTINATION_IP",
  "SIGNATURE",
];

export function parseEntityDimension(value: string | undefined): IpsDimensionName | undefined {
  return ENTITY_DIMENSIONS.find((d) => d === value);
}

export interface IpsSourceQuery {
  range: IpsRangeKey;
  series: SourceSeriesKey;
  dimension: IpsDimensionName | undefined;
}

/** The query string of a state with every default left out, so a link stays short. */
export function sourceHref(id: string, q: IpsSourceQuery, change: Partial<IpsSourceQuery> = {}) {
  const next = { ...q, ...change };
  const qs = new URLSearchParams();
  if (next.range !== "24h") qs.set("range", next.range);
  if (next.series !== "events") qs.set("series", next.series);
  if (next.dimension) qs.set("dimension", next.dimension);
  const text = qs.toString();
  return `/behavior/ips/sources/${encodeURIComponent(id)}${text ? `?${text}` : ""}`;
}

export function IpsRangeSelector({ id, query }: { id: string; query: IpsSourceQuery }) {
  const t = useTranslations("ips.bsource");
  return (
    <nav className="range-selector" aria-label={t("rangeLabel")}>
      <span className="muted">{t("rangeTitle")}</span>
      {IPS_RANGES.map((r) => (
        <Link
          key={r.key}
          href={sourceHref(id, query, { range: r.key })}
          className={`range-option${r.key === query.range ? " range-option-active" : ""}`}
          aria-current={r.key === query.range ? "page" : undefined}
        >
          {t(`range.${r.key}`)}
        </Link>
      ))}
    </nav>
  );
}

export function IpsSeriesSelector({ id, query }: { id: string; query: IpsSourceQuery }) {
  const t = useTranslations("ips.bsource");
  return (
    <nav className="range-selector" aria-label={t("seriesLabel")}>
      <span className="muted">{t("seriesLabel")}</span>
      {SOURCE_SERIES_KEYS.map((k) => (
        <Link
          key={k}
          href={sourceHref(id, query, { series: k })}
          className={`range-option${k === query.series ? " range-option-active" : ""}`}
          aria-current={k === query.series ? "page" : undefined}
        >
          {t(`series.${k}`)}
        </Link>
      ))}
    </nav>
  );
}

export function IpsDimensionFilter({ id, query }: { id: string; query: IpsSourceQuery }) {
  const t = useTranslations("ips.bsource.entities");
  const i18n = useBehaviorI18n();
  return (
    <nav className="range-selector ips-filter" aria-label={t("filterLabel")}>
      <span className="muted">{t("show")}</span>
      <Link
        href={sourceHref(id, query, { dimension: undefined })}
        className={`range-option${query.dimension ? "" : " range-option-active"}`}
        aria-current={query.dimension ? undefined : "page"}
      >
        {t("all")}
      </Link>
      {ENTITY_DIMENSIONS.map((d) => (
        <Link
          key={d}
          href={sourceHref(id, query, { dimension: d })}
          className={`range-option${d === query.dimension ? " range-option-active" : ""}`}
          aria-current={d === query.dimension ? "page" : undefined}
        >
          {wordOrValue(i18n, "ips.behavior.dim", d)}
        </Link>
      ))}
    </nav>
  );
}
