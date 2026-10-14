"use client";

// KPI card 4 (UR.4.2): active anomalies. Drills down to /anomalies?active_only=true.
//
// The breakdown by type comes from the summary and is complete. The breakdown by
// severity is the severity chart's job (UI-2.3), so the capped active list is not
// read here. The sources that are not judged are named beside the count: a count
// of zero over unbaselined sources is the absence of detection, not of problems.

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads, type AnomalySummary, type Page } from "@/lib/api";
import { anomaliesCard, type AnomaliesCard } from "@/lib/home/anomalies";

import { Figure, KpiFrame, Line, n } from "./KpiFrame";

// The derivation takes the active list for its severity counts, which this card
// does not show; an empty page keeps its signature without a second read.
const NO_LIST: Page<AnomalySummary> = { items: [], total: 0, limit: 200, offset: 0 };

const SHORT: Record<AnomaliesCard["byType"][number]["key"], string> = {
  spikes: "spikes",
  drops: "drops",
  silent: "silent",
  candidates: "candidates",
  recovering: "recovering",
};

function Body({ card }: { card: AnomaliesCard }) {
  return (
    <>
      <Figure value={n(card.open)} unit="active anomalies" />
      <Line parts={card.byType.map((t) => `${n(t.count)} ${SHORT[t.key]}`)} />
      <p className="kpi-note" title="A source without a baseline gets no verdict, so it cannot raise an anomaly.">
        {card.notJudgedText}; {card.notJudged === 1 ? "it is" : "they are"} not in these counts.
      </p>
    </>
  );
}

export function KpiAnomalies() {
  const summaryRead = homeReads.anomalySummary();
  const state = useApiResource(summaryRead.key, summaryRead.fetch);
  return (
    <KpiFrame
      id="anomalies"
      title="Active anomalies, now"
      href="/anomalies?active_only=true"
      linkLabel="View active anomalies"
      linkText="Anomalies"
      what="anomalies"
      permission="read:anomalies"
      state={state}
      description="Anomalies in an active state (candidate, open or recovering), by type. Sources without a baseline are not judged and are not counted."
    >
      {(summary) => <Body card={anomaliesCard(summary, NO_LIST)} />}
    </KpiFrame>
  );
}
