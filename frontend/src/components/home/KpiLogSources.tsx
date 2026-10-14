"use client";

// KPI card 2 (UR.4.2): log sources, judged and not judged. Drills down to
// /behavior/sources.
//
// "n of m judged normal", where judged excludes INSUFFICIENT_DATA; never a
// healthy percentage. The total log sources QRadar knows comes from /overview
// and is optional: without it the card still states what it monitors.

import { HealthDistribution } from "@/components/behavior/HealthDistribution";
import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { healthSummaryText } from "@/lib/behaviorOverview";
import { combine2 } from "@/lib/home/combine";
import { logSources, type LogSourcesCard } from "@/lib/home/logSources";

import { Figure, KpiFrame, Line, Qualifiers, n } from "./KpiFrame";

function Body({ card, totalNote }: { card: LogSourcesCard; totalNote: string | null }) {
  const total = card.total === null ? null : n(card.total);
  return (
    <>
      <Figure
        value={n(card.monitored)}
        unit={total ? `of ${total} monitored` : "monitored"}
        title={total ? `Monitored log sources, of ${total} that QRadar knows.` : undefined}
      />
      <Qualifiers items={card.qualifiers} />
      <Line
        title={card.silentDefinition}
        parts={[
          card.judgedText ?? "\u2014 judged normal (no source is judged yet)",
          `${n(card.silent)} silent`,
        ]}
      />
      {totalNote ? <p className="kpi-note">{totalNote}</p> : null}
      <HealthDistribution
        groups={card.groups}
        total={card.monitored}
        summary={healthSummaryText(card.groups, card.monitored, card.silent)}
      />
    </>
  );
}

export function KpiLogSources() {
  const summaryRead = homeReads.anomalySummary();
  const sourcesRead = homeReads.sourceBehaviors();
  const overviewRead = homeReads.overview();
  const summary = useApiResource(summaryRead.key, summaryRead.fetch);
  const sources = useApiResource(sourcesRead.key, sourcesRead.fetch);
  const overview = useApiResource(overviewRead.key, overviewRead.fetch);
  const state = combine2(summary, sources);

  let total: number | null = null;
  let totalNote: string | null = null;
  if (overview.status === "ok") {
    total = overview.data.log_sources.total_log_sources;
  } else if (overview.status === "forbidden") {
    totalNote = "Total log sources not shown: your role cannot read it (permission read:overview).";
  } else if (overview.status === "error") {
    totalNote = "Total log sources unavailable.";
  }

  return (
    <KpiFrame
      id="log-sources"
      title="Log sources, now"
      href="/behavior/sources"
      linkLabel="View log sources"
      linkText="Sources"
      what="log source health"
      permission="read:anomalies"
      state={state}
      description="Monitored log sources and how many have a verdict. Judged excludes sources with insufficient data. Silent means the detector saw no events where some were expected (NO_EVENTS anomaly)."
    >
      {([s, list]) => <Body card={logSources(s, list, total)} totalNote={totalNote} />}
    </KpiFrame>
  );
}
