"use client";

// WAF sources (UR.5.1): are the WAF sources collecting? Per source: its profile verdict for
// the current profile version, the latest bucket's endpoint coverage and the active endpoints
// against the ceiling. A source with no collected bucket has no coverage (an em dash).

import Link from "next/link";
import { useTranslations } from "next-intl";

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { wafSourcesAsOf } from "@/lib/home/asOf";
import { WidgetFrame, WidgetQualifiers } from "@/components/home/WidgetFrame";
import { Unmeasured } from "@/components/home/WidgetParts";
import { TableScroll } from "@/components/ui/TableScroll";
import { truncated } from "@/lib/home/qualifiers";
import { formatPercent } from "@/lib/format";
import { atCeiling, sortByAttention, validationView } from "@/components/waf/wafSourceHealth";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { formatCount } from "@/lib/format";

import { useExpanded } from "@/widgets/runtime";

import "./catalog.css";

export const WAF_SOURCE_ROWS = 6;

export function WafSources() {
  const t = useTranslations("waf.sourcesWidget");
  const i18n = useBehaviorI18n();
  const expanded = useExpanded();
  const read = homeReads.wafSources();
  const state = useApiResource(read.key, read.fetch);

  return (
    <WidgetFrame
      id="waf-sources"
      title={t("title")}
      windowText={t("window")}
      link={{ href: "/behavior/waf", label: t("linkWaf") }}
      state={state}
      dataTo={{
        at: state.status === "ok" ? wafSourcesAsOf(state.data.items) : null,
        family: "WAF_COLLECTION",
      }}
      what={t("what")}
      permission="read:anomalies"
    >
      {({ data }) => {
        if (data.items.length === 0) {
          return (
            <p className="tw-state">
              {t("empty")}
            </p>
          );
        }
        const sorted = sortByAttention(data.items);
        const rows = expanded ? sorted : sorted.slice(0, WAF_SOURCE_ROWS);
        return (
          <>
            <TableScroll label={t("tableLabel")}>
              <table className="tw-table cw-table" aria-label={t("tableLabel")}>
                <colgroup>
                  <col />
                  <col className="tw-col-badge" />
                  <col className="tw-col-ratio" />
                  <col className="tw-col-eps" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">{t("source")}</th>
                    <th scope="col">{t("profile")}</th>
                    <th scope="col" className="num">
                      {t("coverage")}
                    </th>
                    <th scope="col" className="num">
                      {t("endpoints")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => {
                    const v = validationView(s, i18n);
                    const b = s.latest_bucket;
                    return (
                      <tr key={s.log_source_id}>
                        <td className="tw-name">
                          <Link
                            href={`/behavior/waf/sources/${s.log_source_id}`}
                            title={s.log_source_name}
                          >
                            {s.log_source_name}
                          </Link>
                          {!s.enabled ? <span className="tw-sub">{t("disabled")}</span> : null}
                        </td>
                        <td title={v.detail}>
                          <span className={`tw-state-word ${v.tone}`}>{v.label}</span>
                        </td>
                        <td className="num">
                          {b === null ? (
                            <Unmeasured reason={t("noBucket")} />
                          ) : (
                            formatPercent(
                              b.endpoint_coverage_ratio,
                              b.coverage_measurement,
                              1,
                              i18n.locale,
                            )
                          )}
                        </td>
                        <td className="num cw-nowrap">
                          <span className={atCeiling(s) ? "tw-state-word warn" : undefined}>
                            {formatCount(s.active_endpoints, i18n.locale)}
                          </span>
                          <span className="tw-sub">
                            {t("of", {
                              ceiling: formatCount(s.active_endpoint_ceiling, i18n.locale),
                            })}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableScroll>
            <WidgetQualifiers
              qualifiers={data.total > data.items.length ? [truncated(data.items.length, data.total)] : []}
            />
            {expanded || sorted.length > rows.length ? (
              <p className="tw-note">
                {t("note", {
                  rows: formatCount(rows.length, i18n.locale),
                  total: formatCount(sorted.length, i18n.locale),
                })}
              </p>
            ) : null}
          </>
        );
      }}
    </WidgetFrame>
  );
}
