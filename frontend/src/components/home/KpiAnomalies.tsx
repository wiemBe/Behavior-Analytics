"use client";

// KPI card 4 (UR.4.2): active anomalies. Drills down to /anomalies?active_only=true.
//
// Detector and lifecycle are two independent partitions of the same total (every active
// anomaly has one detector and one state). Each line is labelled with its dimension so
// the two cannot be read as one partition; "other" is every detector not listed.
// Severity remains the chart's job, so the capped active list is not read here.

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads, type AnomalySummary, type Page } from "@/lib/api";
import { anomaliesCard, type AnomaliesCard } from "@/lib/home/anomalies";

import { countNoun, Figure, Line, n } from "./KpiParts";
import { WidgetFrame, WidgetQualifiers } from "./WidgetFrame";

// The derivation takes the active list for its severity counts, which this card
// does not show; an empty page keeps its signature without a second read.
const NO_LIST: Page<AnomalySummary> = { items: [], total: 0, limit: 200, offset: 0 };

const SHORT: Record<AnomaliesCard["byDetector"][number]["key"], string> = {
  spikes: "spikes",
  drops: "drops",
  silent: "silent",
  other: "other",
  open: "open",
  candidates: "candidates",
  recovering: "recovering",
};

function Body({ card }: { card: AnomaliesCard }) {
  return (
    <>
      <Figure value={n(card.open)} unit="active anomalies" />
      <Line
        label="By detector"
        parts={card.byDetector.map((t) => countNoun(t.count, SHORT[t.key]))}
      />
      <Line
        label="Lifecycle"
        parts={card.byLifecycle.map((t) => countNoun(t.count, SHORT[t.key]))}
      />
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
    <WidgetFrame
      variant="kpi"
      id="kpi-anomalies"
      title="Active anomalies, now"
      link={{
        href: "/anomalies?active_only=true",
        label: "Anomalies",
        ariaLabel: "View active anomalies",
      }}
      what="anomalies"
      permission="read:anomalies"
      state={state}
      description="Anomalies in an active state (candidate, open or recovering), by type. Sources without a baseline are not judged and are not counted."
    >
      {({ data: summary }) =>
        summary.monitored_sources === 0 &&
        summary.open_anomalies === 0 &&
        summary.insufficient_data_sources === 0 ? (
          <>
            <Figure value={null} unit="active anomalies" />
            <WidgetQualifiers
              variant="kpi"
              qualifiers={[
                {
                  kind: "INSUFFICIENT_DATA",
                  reason: "No monitored log source; there is nothing to judge yet.",
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
