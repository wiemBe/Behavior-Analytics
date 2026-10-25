"use client";

// KPI card 1 (UR.4.2): total EPS, a mean over the widget's range (24 h by default), with its
// expectation, its largest class and the trend. Drills down to /fleet.
//
// The headline and the breakdown come from /fleet/summary and are the card; the
// trend comes from /fleet/eps-series and is context, so its failure leaves the
// card standing and says only that the trend is unavailable.

import { useTranslations } from "next-intl";

import { useApiResource, type ResourceState } from "@/hooks/useApiResource";
import { useEnumLabels } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { useMeasures } from "@/i18n/measures";
import { homeReads, type FleetEpsSeries } from "@/lib/api";
import { fleetDrillHref } from "@/lib/fleet";
import { totalEps, type TotalEps } from "@/lib/home/totalEps";
import { useWidgetCopy } from "@/widgets/copy";
import { useEffectiveRange, type RangedProps } from "@/widgets/runtime";

import { Figure, Line } from "./KpiParts";
import { reasonText } from "./qualifierText";
import { WidgetFrame, WidgetQualifiers } from "./WidgetFrame";
import { Sparkline } from "./Sparkline";

function Trend({ state }: { state: ResourceState<FleetEpsSeries> }) {
  const t = useTranslations("home.kpiEps");
  if (state.status === "ok") return <Sparkline series={state.data} />;
  if (state.status === "idle" || state.status === "loading") {
    return (
      <p className="kpi-note" role="status" aria-live="polite">
        {t("trendLoading")}
      </p>
    );
  }
  return (
    <p className="kpi-note">
      {state.status === "forbidden"
        ? t("trendForbidden")
        : t("trendUnavailable", {
            status: state.httpStatus !== null ? ` (HTTP ${state.httpStatus})` : "",
          })}
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
  const t = useTranslations("home.kpiEps");
  const units = useTranslations("units");
  const say = asTextTranslator(useTranslations());
  const measures = useMeasures();
  const { label } = useEnumLabels();
  const eps = (v: number) => measures.eps(v);
  const mean = card.meanEps === null ? null : eps(card.meanEps);
  const insufficient = card.qualifiers.find((q) => q.kind === "INSUFFICIENT_DATA");
  const lostClasses =
    card.meanEps !== null && card.classesWithoutData > 0
      ? t("classesNotInSum", { count: card.classesWithoutData })
      : undefined;
  const cov = card.expectedCoverage;
  const compared =
    card.comparableMeanEps !== null && cov?.partialFleet
      ? t("comparedPartial", { eps: eps(card.comparableMeanEps) })
      : card.comparableMeanEps !== null
        ? t("compared", { eps: eps(card.comparableMeanEps) })
        : undefined;
  return (
    <>
      <Figure
        value={mean}
        unit={units("eps")}
        reason={
          insufficient?.kind === "INSUFFICIENT_DATA" ? reasonText(say, insufficient) : undefined
        }
        title={t("meanOver", { window: windowText })}
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
            ? t("expectedNone")
            : t("expected", { eps: eps(card.expectedEps) }),
          card.vsExpectedPct !== null && measures.signedPercent(card.vsExpectedPct),
        ]}
      />
      <Line
        title={
          card.topClass
            ? t("topClassTitle", {
                label: label("sourceClass", card.topClass.sourceClass),
                eps: eps(card.topClass.eps),
              })
            : undefined
        }
        parts={[
          cov &&
            t("baselines", {
              have: measures.count(cov.baselinedSources),
              total: measures.count(cov.monitoredSources),
            }),
          card.topClass
            ? t("largest", {
                label: label("sourceClass", card.topClass.sourceClass),
                share: card.topClass.share !== null ? ` ${measures.percent(card.topClass.share, 0)}` : "",
              })
            : t("largestNone"),
        ]}
      />
      <Trend state={trend} />
    </>
  );
}

export function KpiTotalEps({ range }: RangedProps = {}) {
  const t = useTranslations("home.kpiEps");
  const copy = useWidgetCopy();
  const window = useEffectiveRange(range);
  const windowText = copy.range(window);
  const summary = homeReads.fleetSummary(window);
  const series = homeReads.epsSeries(window, "none");
  const state = useApiResource(summary.key, summary.fetch);
  const trend = useApiResource(series.key, series.fetch);
  return (
    <WidgetFrame
      variant="kpi"
      id="kpi-total-eps"
      title={t("title", { window: windowText })}
      link={{ href: fleetDrillHref(window), label: t("link"), ariaLabel: t("linkLabel") }}
      what={t("what")}
      permission="read:fleet"
      state={state}
      description={t("description", { window: windowText })}
    >
      {({ data }) => <Body card={totalEps(data)} trend={trend} windowText={windowText} />}
    </WidgetFrame>
  );
}
