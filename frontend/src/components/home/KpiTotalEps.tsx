"use client";

// KPI card 1 (UR.4.2): total EPS, a mean over the widget's range (24 h by default), with its
// expectation, its largest class and the trend. Drills down to /fleet.
//
// The headline and the breakdown come from /fleet/summary and are the card; the
// trend comes from /fleet/eps-series and is context, so its failure leaves the
// card standing and says only that the trend is unavailable.

import { useApiResource, type ResourceState } from "@/hooks/useApiResource";
import { homeReads, type FleetEpsSeries } from "@/lib/api";
import { fleetDrillHref, formatEpsValue } from "@/lib/fleet";
import { formatPercent } from "@/lib/format";
import { totalEps, type TotalEps } from "@/lib/home/totalEps";
import { RANGE_TEXT, useEffectiveRange, type RangedProps } from "@/widgets/runtime";

import { signedPercent } from "@/lib/home/format";

import { Figure, Line, n } from "./KpiParts";
import { WidgetFrame, WidgetQualifiers } from "./WidgetFrame";
import { Sparkline } from "./Sparkline";

const eps = (v: number) => formatEpsValue(v) as string;

function Trend({ state }: { state: ResourceState<FleetEpsSeries> }) {
  if (state.status === "ok") return <Sparkline series={state.data} />;
  if (state.status === "idle" || state.status === "loading") {
    return (
      <p className="kpi-note" role="status" aria-live="polite">
        Loading trend…
      </p>
    );
  }
  return (
    <p className="kpi-note">
      {state.status === "forbidden"
        ? "Trend not shown: your role cannot read it (permission read:fleet)."
        : `Trend unavailable${state.httpStatus !== null ? ` (HTTP ${state.httpStatus})` : ""}.`}
    </p>
  );
}

function Body({
  card,
  trend,
  windowText,
}: {
  card: TotalEps;
  trend: ResourceState<FleetEpsSeries>;
  windowText: string;
}) {
  const mean = card.meanEps === null ? null : eps(card.meanEps);
  const insufficient = card.qualifiers.find((q) => q.kind === "INSUFFICIENT_DATA");
  const lostClasses =
    card.meanEps !== null && card.classesWithoutData > 0
      ? `${card.classesWithoutData} class${card.classesWithoutData === 1 ? "" : "es"} not in sum`
      : undefined;
  const cov = card.expectedCoverage;
  const compared =
    card.comparableMeanEps !== null && cov?.partialFleet
      ? `Compared on baselined classes only: observed ${eps(card.comparableMeanEps)} EPS.`
      : card.comparableMeanEps !== null
        ? `Observed ${eps(card.comparableMeanEps)} EPS on baselined classes.`
        : undefined;
  return (
    <>
      <Figure
        value={mean}
        unit="EPS"
        reason={insufficient?.kind === "INSUFFICIENT_DATA" ? insufficient.reason : undefined}
        title={`Mean over ${windowText}, complete intervals only.`}
      />
      <WidgetQualifiers
        variant="kpi"
        qualifiers={card.qualifiers.filter((q) => q.kind !== "INSUFFICIENT_DATA")}
        extra={lostClasses}
      />
      <Line
        title={compared}
        parts={[
          card.expectedEps === null
            ? "expected \u2014 (no reliable baseline)"
            : `expected ${eps(card.expectedEps)} EPS`,
          card.vsExpectedPct !== null && signedPercent(card.vsExpectedPct),
        ]}
      />
      <Line
        title={
          card.topClass
            ? `${card.topClass.label}, ${eps(card.topClass.eps)} EPS of the total`
            : undefined
        }
        parts={[
          cov && `baselines ${n(cov.baselinedSources)} of ${n(cov.monitoredSources)} sources`,
          card.topClass
            ? `largest: ${card.topClass.label}${
                card.topClass.share !== null
                  ? ` ${formatPercent(card.topClass.share, null, 0)}`
                  : ""
              }`
            : "largest: \u2014 (no class measured)",
        ]}
      />
      <Trend state={trend} />
    </>
  );
}

export function KpiTotalEps({ range }: RangedProps = {}) {
  const window = useEffectiveRange(range);
  const windowText = RANGE_TEXT[window];
  const summary = homeReads.fleetSummary(window);
  const series = homeReads.epsSeries(window, "none");
  const state = useApiResource(summary.key, summary.fetch);
  const trend = useApiResource(series.key, series.fetch);
  return (
    <WidgetFrame
      variant="kpi"
      id="kpi-total-eps"
      title={`Total EPS, ${windowText}`}
      link={{ href: fleetDrillHref(window), label: "Fleet", ariaLabel: "View fleet by class" }}
      what="fleet EPS"
      permission="read:fleet"
      state={state}
      description={`Mean EPS over ${windowText}, summed over source classes, complete intervals only. Expected is the sum of the baselines of the classes that have one.`}
    >
      {({ data }) => <Body card={totalEps(data)} trend={trend} windowText={windowText} />}
    </WidgetFrame>
  );
}
