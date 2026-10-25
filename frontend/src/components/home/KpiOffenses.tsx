"use client";

// KPI card 3 (UR.4.2): open offenses. Drills down to /offenses?status=OPEN.
//
// A null analytics count is "not collected" on its own, so the card does not
// wait for the probe in that case. A numeric zero still needs the probe: a probe
// that cannot be read is passed as null and the derivation fails closed
// (NOT_COLLECTED), so a failed probe never turns into "no open offenses".

import { useTranslations } from "next-intl";

import { useApiResource } from "@/hooks/useApiResource";
import { useMeasures } from "@/i18n/measures";
import { homeReads } from "@/lib/api";
import { countIsUnknown, offensesCard, type OffensesCard } from "@/lib/home/offenses";

import { countNoun, Figure, Line } from "./KpiParts";
import { WidgetFrame, WidgetQualifiers } from "./WidgetFrame";

function Body({ card }: { card: OffensesCard }) {
  const t = useTranslations("home.kpiOffenses");
  const { count, locale } = useMeasures();
  if (card.open === null) {
    return (
      <>
        <Figure value={null} unit={t("unit")} />
        <WidgetQualifiers variant="kpi" qualifiers={card.qualifiers} />
      </>
    );
  }
  const b = card.breakdown;
  const nbsp = "\u00a0";
  return (
    <>
      <Figure value={count(card.open)} unit={t("unit")} />
      <Line
        title={t("criticalMeans", { magnitude: card.criticalMagnitude })}
        parts={[
          `${card.critical === null ? "\u2014" : countNoun(card.critical, t("critical"), locale)} (\u2265 ${card.criticalMagnitude})`,
          b && b.below !== null
            ? countNoun(b.below, t("below"), locale)
            : `\u2014${nbsp}${t("below")}`,
        ]}
      />
      {b && b.unknown > 0 && (
        <Line
          title={t("unknownMagnitudeTitle")}
          parts={[t("unknownMagnitude", { count: count(b.unknown) })]}
        />
      )}
      <Line
        parts={[
          card.unassigned === null
            ? `\u2014${nbsp}${t("unassigned")}`
            : countNoun(card.unassigned, t("unassigned"), locale),
          card.exceedingSla === null
            ? `\u2014${nbsp}${t("overSla")}`
            : countNoun(card.exceedingSla, t("overSla"), locale),
        ]}
      />
      {card.ageUnknown !== null && card.ageUnknown > 0 && (
        <Line
          title={t("unknownAgeTitle")}
          parts={[t("unknownAge", { count: count(card.ageUnknown) })]}
        />
      )}
    </>
  );
}

export function KpiOffenses() {
  const t = useTranslations("home.kpiOffenses");
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
      title={t("title")}
      link={{
        href: "/offenses?status=OPEN",
        label: t("link"),
        ariaLabel: t("linkLabel"),
      }}
      what={t("what")}
      permission="read:offenses"
      description={t("description")}
      state={probePending && state.status === "ok" ? { status: "loading" } : state}
    >
      {({ data }) => <Body card={offensesCard(data, probeData)} />}
    </WidgetFrame>
  );
}
