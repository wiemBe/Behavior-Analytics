"use client";

// KPI card 4 (UR.4.2): active anomalies. Drills down to /anomalies?active_only=true.
//
// Detector and lifecycle are two independent partitions of the same total (every active
// anomaly has one detector and one state). Each line is labelled with its dimension so
// the two cannot be read as one partition; "other" is every detector not listed.
// Severity remains the chart's job, so the capped active list is not read here.

import { useTranslations } from "next-intl";

import { useApiResource } from "@/hooks/useApiResource";
import { useMeasures } from "@/i18n/measures";
import { homeReads, type AnomalySummary, type Page } from "@/lib/api";
import { anomaliesCard, type AnomaliesCard } from "@/lib/home/anomalies";

import { countNoun, Figure, Line } from "./KpiParts";
import { WidgetFrame, WidgetQualifiers } from "./WidgetFrame";

// The derivation takes the active list for its severity counts, which this card
// does not show; an empty page keeps its signature without a second read.
const NO_LIST: Page<AnomalySummary> = { items: [], total: 0, limit: 200, offset: 0 };

function Body({ card }: { card: AnomaliesCard }) {
  const t = useTranslations("home.kpiAnomalies");
  const { count, locale } = useMeasures();
  // The label of a count is looked up by the card's key, never by its English label.
  const noun = (key: AnomaliesCard["byDetector"][number]["key"]) => t(`short.${key}`);
  return (
    <>
      <Figure value={count(card.open)} unit={t("unit")} />
      <Line
        label={t("byDetector")}
        parts={card.byDetector.map((entry) => countNoun(entry.count, noun(entry.key), locale))}
      />
      <Line
        label={t("lifecycle")}
        parts={card.byLifecycle.map((entry) => countNoun(entry.count, noun(entry.key), locale))}
      />
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
        summary.open_anomalies === 0 &&
        summary.insufficient_data_sources === 0 ? (
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
