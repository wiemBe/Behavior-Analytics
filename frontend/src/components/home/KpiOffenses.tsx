"use client";

// KPI card 3 (UR.4.2): open offenses. Drills down to /offenses?status=OPEN.
//
// A null analytics count is "not collected" on its own, so the card does not
// wait for the probe in that case. A numeric zero still needs the probe: a probe
// that cannot be read is passed as null and the derivation fails closed
// (NOT_COLLECTED), so a failed probe never turns into "no open offenses".

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { countIsUnknown, offensesCard, type OffensesCard } from "@/lib/home/offenses";

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
        ]}
      />
      {b && b.unknown > 0 && (
        <Line
          title="Open offenses with no magnitude: in neither the critical nor the below count."
          parts={[`+\u00a0${n(b.unknown)}\u00a0with unknown magnitude`]}
        />
      )}
      <Line
        parts={[
          card.unassigned === null ? "\u2014\u00a0unassigned" : countNoun(card.unassigned, "unassigned"),
          card.exceedingSla === null ? "\u2014\u00a0over SLA" : countNoun(card.exceedingSla, "over SLA"),
        ]}
      />
      {card.ageUnknown !== null && card.ageUnknown > 0 && (
        <Line
          title="Open offenses with no age: not in the over SLA count."
          parts={[`+\u00a0${n(card.ageUnknown)}\u00a0with unknown age`]}
        />
      )}
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
  // A null count is already unknown. A numeric zero waits for the probe, so a
  // probe still on its way is not read as "no open offenses".
  const countUnknown = state.status === "ok" && countIsUnknown(state.data.aggregates.active);
  const probePending =
    !countUnknown && (probe.status === "idle" || probe.status === "loading");
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
      description="Offenses open in the latest collected snapshot. Critical is magnitude at or above the product's threshold; an offense with no magnitude, or no age, is counted apart from those counts."
      state={probePending && state.status === "ok" ? { status: "loading" } : state}
    >
      {({ data }) => <Body card={offensesCard(data, probeData)} />}
    </WidgetFrame>
  );
}
