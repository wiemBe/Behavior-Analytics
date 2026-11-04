"use client";

// KPI card 4 (UR.4.2): active anomalies. Drills down to /anomalies?active_only=true.
//
// The total counts every family. Family and lifecycle are two partitions of it (every active
// anomaly has one family and one state): the log sources split by detector, with WAF, IPS and
// property watches as their own parts. A family that could not be read is named as not read
// and the total becomes a lower bound. Severity remains the chart's job, so the capped active
// list is not read here.

import { useTranslations } from "next-intl";

import { useApiResource } from "@/hooks/useApiResource";
import { useMeasures } from "@/i18n/measures";
import { homeReads, type AnomalySummary, type Page } from "@/lib/api";
import {
  anomaliesCard,
  type AnomaliesCard,
  type FamilyCount,
  type TypeCount,
} from "@/lib/home/anomalies";

import { Figure, Line } from "./KpiParts";
import { WidgetFrame, WidgetQualifiers } from "./WidgetFrame";

// The derivation takes the active list for its severity counts, which this card
// does not show; an empty page keeps its signature without a second read.
const NO_LIST: Page<AnomalySummary> = { items: [], total: 0, limit: 200, offset: 0 };

function Body({ card }: { card: AnomaliesCard }) {
  const t = useTranslations("home.kpiAnomalies");
  const { count } = useMeasures();
  // Each part is one ICU plural message, looked up by the card's key, never by its English label.
  const part = (entry: TypeCount) => t(`count.${entry.key}`, { count: entry.count });
  const family = (entry: FamilyCount) =>
    entry.count === null
      ? t("familyNotRead", { family: t(`family.${entry.key}`) })
      : t(`count.${entry.key}`, { count: entry.count });
  const total = card.open === null ? null : count(card.open);
  return (
    <>
      {total === null ? (
        <Figure value={null} unit={t("unit")} reason={t("notRead")} />
      ) : (
        <Figure value={card.lowerBound ? `\u2265\u00a0${total}` : total} unit={t("unit")} />
      )}
      {card.logSources === null ? null : card.byDetector.length > 0 ? (
        <Line label={t("logSources")} parts={card.byDetector.map(part)} />
      ) : (
        <Line
          label={t("logSources")}
          parts={[t("count.log_source", { count: card.logSources })]}
        />
      )}
      <Line label={t("otherFamilies")} parts={card.families.map(family)} />
      <Line label={t("lifecycle")} parts={card.byLifecycle.map(part)} />
      {card.logSources === null ? (
        <p className="kpi-note">{t("logSourcesNotRead")}</p>
      ) : null}
      {card.lowerBound ? <p className="kpi-note">{t("lowerBound")}</p> : null}
      <p className="kpi-note" title={t("notJudgedTitle")}>
        {t("notJudged", { count: card.notJudged })}
      </p>
    </>
  );
}

export function KpiAnomalies() {
  const t = useTranslations("home.kpiAnomalies");
  const summaryRead = homeReads.anomalySummary();
  const state = useApiResource(summaryRead.key, summaryRead.fetch);
  return (
    <WidgetFrame
      variant="kpi"
      id="kpi-anomalies"
      title={t("title")}
      link={{
        href: "/anomalies?active_only=true",
        label: t("link"),
        ariaLabel: t("linkLabel"),
      }}
      what={t("what")}
      permission="read:anomalies"
      state={state}
      description={t("description")}
    >
      {({ data: summary }) =>
        summary.monitored_sources === 0 &&
        summary.insufficient_data_sources === 0 &&
        anomaliesCard(summary, NO_LIST).open === 0 ? (
          <>
            <Figure value={null} unit={t("unit")} />
            <WidgetQualifiers
              variant="kpi"
              qualifiers={[
                {
                  kind: "INSUFFICIENT_DATA",
                  reason: t("nothingToJudge"),
                },
              ]}
            />
          </>
        ) : (
          <Body card={anomaliesCard(summary, NO_LIST)} />
        )
      }
    </WidgetFrame>
  );
}
