"use client";

// Recent anomalies (UR.4.3): the latest active anomalies and how many are active.
//
// The badge is the response's `total`, worded "n active". An empty list is not
// "all clear": it says how many sources are not being judged. The unified list
// includes WAF anomalies, which are named by their endpoint.

import { useTranslations } from "next-intl";
import Link from "next/link";

import { TableScroll } from "@/components/ui/TableScroll";
import { useApiResource } from "@/hooks/useApiResource";
import { useEnumLabels } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { useMeasures } from "@/i18n/measures";
import { homeReads, sevTone, type SubjectKind } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { RECENT_ANOMALIES_LIMIT, recentAnomalies } from "@/lib/home/recentAnomalies";

import { useExpanded } from "@/widgets/runtime";
import { UtcTime } from "@/components/ui/UtcTime";


import { secondary, WidgetFrame, WidgetLoading } from "./WidgetFrame";
import { Unmeasured, When } from "./WidgetParts";

/** Why an empty WAF list is not a clean bill (English; the widget shows `home.recent.wafEmpty`). */
export const WAF_EMPTY =
  "No active WAF endpoint anomalies. WAF sources without a valid profile are not collected, so they are not judged.";

/**
 * `subject` narrows the list to one kind of subject (the WAF endpoint widget). The default
 * is the Home's list of every active anomaly. The not-judged count is about log sources, so a
 * WAF list neither reads it nor quotes it.
 */
export function RecentAnomalies({ subject }: { subject?: SubjectKind } = {}) {
  const t = useTranslations("home.recent");
  const say = asTextTranslator(useTranslations());
  const measures = useMeasures();
  const { label, severityPill, statePill } = useEnumLabels();
  const waf = subject === "WAF_ENDPOINT";
  const expanded = useExpanded();
  const listRead = waf
    ? homeReads.activeWafAnomalies(RECENT_ANOMALIES_LIMIT)
    : homeReads.activeAnomalies(RECENT_ANOMALIES_LIMIT);
  const summaryRead = homeReads.anomalySummary();
  const list = useApiResource(listRead.key, listRead.fetch);
  const summary = useApiResource(waf ? null : summaryRead.key, summaryRead.fetch);
  const ok = list.status === "ok" ? recentAnomalies(list.data, secondary(summary, say).data) : null;
  // The type of each row by its id: the label comes from the catalog by value; a type the
  // catalog lacks keeps the label the derivation gave it.
  const typeOf = new Map(list.status === "ok" ? list.data.items.map((a) => [a.id, a.anomaly_type]) : []);
  const typeText = (id: string, fallback: string) => {
    const type = typeOf.get(id);
    const word = type ? label("anomalyType", type) : null;
    return word && word !== type ? word : fallback;
  };
  const tableLabel = waf ? t("wafTitle") : t("title");

  return (
    <WidgetFrame
      id={waf ? "recent-waf-anomalies" : "recent-anomalies"}
      title={tableLabel}
      windowText={t("activeNow")}
      badge={ok ? t("badge", { count: measures.count(ok.active) }) : undefined}
      link={
        waf
          ? { href: "/anomalies?subject_kind=WAF_ENDPOINT&active_only=true", label: t("link") }
          : { href: "/anomalies?active_only=true", label: t("link") }
      }
      state={list}
      what={waf ? t("wafWhat") : t("what")}
      permission="read:anomalies"
    >
      {({ data }) => {
          const sum = secondary(summary, say);
          // The empty state quotes the not-judged count, so wait for it.
          if (!waf && data.items.length === 0 && sum.pending) {
            return <WidgetLoading what={t("notJudgedWhat")} />;
          }
          const view = recentAnomalies(data, sum.data);
          if (waf && data.items.length === 0) return <p className="tw-state">{t("wafEmpty")}</p>;
          if (view.empty !== null) {
            return (
              <p className="tw-state">
                {sum.data === null
                  ? t("emptyUnread")
                  : t("emptyNotJudged", { count: sum.data.insufficient_data_sources })}
              </p>
            );
          }
          return (
            <>
              <TableScroll label={tableLabel}>
                <table className="tw-table" aria-label={tableLabel}>
                  <colgroup>
                    <col className="tw-col-time tw-unfold" />
                    <col />
                    <col className="tw-col-badge tw-unfold" />
                    <col className="tw-col-ratio" />
                    <col className="tw-col-action" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col" className="tw-unfold">
                        {t("detected")}
                      </th>
                      <th scope="col" className="tw-lead-col">
                        {t("anomaly")}
                      </th>
                      <th scope="col" className="tw-unfold">
                        {t("severityState")}
                      </th>
                      <th scope="col" className="num">
                        {t("deviation")}
                        <span className="tw-sub tw-wrap">{t("vsExpected")}</span>
                      </th>
                      <th scope="col">
                        <span className="sr-only">{t("investigate")}</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.rows.map((r) => (
                      <tr key={r.id}>
                        <td className="tw-unfold">
                          <When iso={r.detectedAt} />
                        </td>
                        <td className="tw-name tw-lead-col">
                          <Link href={r.sourceHref} title={r.subject}>
                            {r.subject}
                          </Link>
                          <span className="tw-sub" title={r.sourceName ?? undefined}>
                            {typeText(r.id, r.typeLabel)}
                            {r.sourceName !== null && ` · ${r.sourceName}`}
                          </span>
                          {/* Below the widget's breakpoint the Detected and Severity columns
                              fold into this line; above it the line is hidden. */}
                          <span className="tw-sub tw-wrap tw-fold-line">
                            <span className={`tw-state-word ${sevTone(r.severity)}`}>
                              {severityPill(r.severity)}
                            </span>
                            {" · "}
                            <span className={`tw-state-word ${stateTone(r.state)}`}>
                              {statePill(r.state)}
                            </span>
                            {" · "}
                            <UtcTime value={r.detectedAt} variant="compact" />
                          </span>
                        </td>
                        <td className="tw-unfold">
                          <span className={`pill ${sevTone(r.severity)}`}>
                            {severityPill(r.severity)}
                          </span>
                          <span className={`tw-sub tw-wrap tw-state-word ${stateTone(r.state)}`}>
                            {statePill(r.state)}
                          </span>
                        </td>
                        <td className="num">
                          {r.deviation === null ? (
                            <Unmeasured reason={t("noDeviation")} />
                          ) : (
                            measures.ratio(r.deviation)
                          )}
                        </td>
                        <td>
                          <Link
                            className="tw-link"
                            href={r.href}
                            aria-label={t("investigateNamed", { subject: r.subject })}
                          >
                            {t("investigate")}
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
              {view.shown && (
                <p className="tw-note">
                  {expanded
                    ? t("showingExpanded", {
                        shown: view.rows.length,
                        total: measures.count(view.active),
                        limit: RECENT_ANOMALIES_LIMIT,
                      })
                    : t("showing", { shown: view.rows.length, total: measures.count(view.active) })}
                </p>
              )}
            </>
          );
      }}
    </WidgetFrame>
  );
}
