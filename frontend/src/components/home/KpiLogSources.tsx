"use client";

// KPI card 2 (UR.4.2): log sources, judged and not judged. Drills down to
// /behavior/sources.
//
// "n of m judged normal", where judged excludes INSUFFICIENT_DATA; never a
// healthy percentage. The total log sources QRadar knows comes from /overview
// and is optional: without it the card still states what it monitors.

import { useTranslations } from "next-intl";

import { HealthDistribution } from "@/components/behavior/HealthDistribution";
import { useApiResource } from "@/hooks/useApiResource";
import { useMeasures } from "@/i18n/measures";
import { asTextTranslator } from "@/i18n/textTranslator";
import { homeReads } from "@/lib/api";
import { sourcesAsOf } from "@/lib/home/asOf";
import { useFamilyTiming } from "@/widgets/cadence";
import { healthSummaryText } from "@/lib/behaviorOverview";
import { combine2 } from "@/lib/home/combine";
import { inventoryHonesty } from "@/lib/home/inventoryHonesty";
import { logSources, type LogSourcesCard } from "@/lib/home/logSources";

import { countNoun, Figure, Line } from "./KpiParts";
import { WidgetFrame, WidgetQualifiers } from "./WidgetFrame";

function Body({ card, totalNote }: { card: LogSourcesCard; totalNote: string | null }) {
  const t = useTranslations("home.kpiSources");
  const say = asTextTranslator(useTranslations());
  const { count, locale } = useMeasures();
  const total = card.total === null ? null : count(card.total);
  const unit = total ? t("ofMonitored", { total }) : t("monitored");
  if (card.monitored === 0) {
    return (
      <>
        <Figure value={count(card.monitored)} unit={unit} />
        <p className="kpi-note">{t("noMonitored")}</p>
        {totalNote ? <p className="kpi-note">{totalNote}</p> : null}
      </>
    );
  }
  return (
    <>
      <Figure
        value={count(card.monitored)}
        unit={unit}
        title={total ? t("monitoredOf", { total }) : undefined}
      />
      <WidgetQualifiers
        variant="kpi"
        qualifiers={card.qualifiers}
        extra={card.evaluationFault ?? undefined}
        extraTitle={card.evaluationFaultDetail ?? undefined}
      />
      <Line
        title={t("silentDefinition")}
        parts={[
          card.normal !== null
            ? t("judgedNormal", { normal: count(card.normal), judged: count(card.judged) })
            : t("noneJudged"),
          countNoun(card.silent, t("silent"), locale),
        ]}
      />
      {totalNote ? <p className="kpi-note">{totalNote}</p> : null}
      <HealthDistribution
        groups={card.groups}
        total={card.monitored}
        summary={healthSummaryText(card.groups, card.monitored, card.silent, say)}
        say={say}
      />
    </>
  );
}

export function KpiLogSources() {
  const t = useTranslations("home.kpiSources");
  const summaryRead = homeReads.anomalySummary();
  const sourcesRead = homeReads.sourceBehaviors();
  const overviewRead = homeReads.overview();
  const summary = useApiResource(summaryRead.key, summaryRead.fetch);
  const sources = useApiResource(sourcesRead.key, sourcesRead.fetch);
  const overview = useApiResource(overviewRead.key, overviewRead.fetch);
  const state = combine2(summary, sources);
  const { bucketSeconds } = useFamilyTiming("PHASE_A_VOLUME");
  const honesty =
    state.status === "ok" && sources.status === "ok"
      ? inventoryHonesty(sources.data.length, overview)
      : "show";
  const frameState =
    honesty === "wait"
      ? ({ status: "loading" } as const)
      : honesty === "failed"
        ? { kind: "error" as const, action: t("inventoryFailed") }
        : honesty === "not_collected"
          ? { kind: "not_collected" as const, reason: t("inventoryMissing") }
          : state;

  let total: number | null = null;
  let totalNote: string | null = null;
  if (overview.status === "ok") {
    total = overview.data.log_sources.total_log_sources;
  } else if (overview.status === "forbidden") {
    totalNote = t("totalForbidden");
  } else if (overview.status === "error") {
    totalNote = t("totalUnavailable");
  }

  return (
    <WidgetFrame
      variant="kpi"
      id="kpi-log-sources"
      title={t("title")}
      link={{ href: "/behavior/sources", label: t("link"), ariaLabel: t("linkLabel") }}
      what={t("what")}
      permission="read:anomalies"
      state={frameState}
      dataTo={{
        at: sources.status === "ok" ? sourcesAsOf(sources.data, bucketSeconds) : null,
        family: "PHASE_A_VOLUME",
      }}
      description={t("description")}
    >
      {({ data: [s, list] }) => <Body card={logSources(s, list, total)} totalNote={totalNote} />}
    </WidgetFrame>
  );
}
