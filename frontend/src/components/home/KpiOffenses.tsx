"use client";

// KPI card 3 (UR.4.2): open offenses. Drills down to /offenses?status=OPEN.
//
// With no offense snapshot the analytics return zeros, identical to "none open";
// the derivation uses a one-row probe to tell them apart. A probe that cannot be
// read is passed as null and the derivation fails closed (NOT_COLLECTED when the
// count is 0), so a failed probe never turns into "no open offenses".

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { offensesCard, type OffensesCard } from "@/lib/home/offenses";

import { countNoun, Figure, Line, n } from "./KpiParts";
import { WidgetFrame, WidgetQualifiers } from "./WidgetFrame";

function Body({ card }: { card: OffensesCard }) {
  if (card.open === null) {
    return (
      <>
        <Figure value={null} unit="open offenses" />
        <WidgetQualifiers variant="kpi" qualifiers={card.qualifiers} />
      </>
    );
  }
  const b = card.breakdown;
  return (
    <>
      <Figure value={n(card.open)} unit="open offenses" />
      <Line
        title={`Critical means magnitude of ${card.criticalMagnitude} or more, the product's own threshold.`}
        parts={[
          `${card.critical === null ? "\u2014" : countNoun(card.critical, "critical")} (\u2265 ${card.criticalMagnitude})`,
          b && b.below !== null ? countNoun(b.below, "below") : "\u2014\u00a0below",
          b && b.unknown !== null && b.unknown > 0 && countNoun(b.unknown, "unknown"),
        ]}
      />
      <Line
        parts={[
          card.unassigned === null ? "\u2014\u00a0unassigned" : countNoun(card.unassigned, "unassigned"),
          card.exceedingSla === null ? "\u2014\u00a0over SLA" : countNoun(card.exceedingSla, "over SLA"),
        ]}
      />
    </>
  );
}

export function KpiOffenses() {
  const analyticsRead = homeReads.offenseAnalytics();
  const probeRead = homeReads.offenseProbe();
  const state = useApiResource(analyticsRead.key, analyticsRead.fetch);
  const probe = useApiResource(probeRead.key, probeRead.fetch);
  // Loading or failed probe: null, not an assumption either way.
  const probeData = probe.status === "ok" ? probe.data : null;
  // Do not decide NOT_COLLECTED from a probe that is still on its way.
  const probePending = probe.status === "idle" || probe.status === "loading";
  return (
    <WidgetFrame
      variant="kpi"
      id="kpi-offenses"
      title="Open offenses, latest snapshot"
      link={{
        href: "/offenses?status=OPEN",
        label: "Offenses",
        ariaLabel: "View open offenses",
      }}
      what="offenses"
      permission="read:offenses"
      description="Offenses open in the latest collected snapshot. Critical is magnitude at or above the product's threshold; unknown is an offense with no magnitude."
      state={probePending && state.status === "ok" ? { status: "loading" } : state}
    >
      {({ data }) => <Body card={offensesCard(data, probeData)} />}
    </WidgetFrame>
  );
}
