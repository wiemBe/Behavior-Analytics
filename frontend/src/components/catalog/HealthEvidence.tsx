"use client";

// Evidence health (UR.5.1): is evidence collection keeping up? Two counts of anomalies, from
// the anomaly summary. A failed count is a problem to look at, so it leads the drill-down.

import { useLocale, useTranslations } from "next-intl";

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { WidgetFrame } from "@/components/home/WidgetFrame";
import { formatCount, type FormatLocale } from "@/lib/format";

import "./catalog.css";

export function HealthEvidence() {
  const t = useTranslations("catalog.evidence");
  const now = useTranslations("catalog")("now");
  const locale = useLocale() as FormatLocale;
  const n = (v: number) => formatCount(v, locale);
  const read = homeReads.anomalySummary();
  const state = useApiResource(read.key, read.fetch);

  return (
    <WidgetFrame
      id="health-evidence"
      title={t("title")}
      windowText={now}
      link={{ href: "/anomalies?evidence_status=FAILED", label: t("link") }}
      state={state}
      what={t("what")}
      permission="read:anomalies"
    >
      {({ data }) => (
        <>
          <table className="tw-table cw-table">
            <caption className="sr-only">{t("caption")}</caption>
            <colgroup>
              <col />
              <col className="tw-col-count" />
            </colgroup>
            <thead>
              <tr>
                <th scope="col">{t("status")}</th>
                <th scope="col" className="num">
                  {t("anomalies")}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{t("pending")}</td>
                <td className="num">{n(data.evidence_pending)}</td>
              </tr>
              <tr>
                <td>
                  <span className={data.evidence_failed > 0 ? "tw-state-word crit" : undefined}>
                    {t("failed")}
                  </span>
                </td>
                <td className="num">{n(data.evidence_failed)}</td>
              </tr>
            </tbody>
          </table>
          <p className="tw-note">
            {data.evidence_failed > 0 ? t("failedNote") : t("okNote")}
          </p>
        </>
      )}
    </WidgetFrame>
  );
}
