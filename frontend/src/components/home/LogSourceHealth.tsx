"use client";

// Log source health (UR.4.3): the fleet's states as `HealthDistribution`, then the
// categories that have real detection logic, each with its count and, where a page
// owns the list, a link to it. There is no "Degraded": nothing detects it.
//
// The figures come from four reads. The states are the widget's own question and
// gate it; each category row stands on its read alone, and a read that failed
// leaves that row at a dash with the reason, never at zero.

import { useTranslations } from "next-intl";
import Link from "next/link";

import { HealthDistribution } from "@/components/behavior/HealthDistribution";
import { TableScroll } from "@/components/ui/TableScroll";
import { useApiResource } from "@/hooks/useApiResource";
import { asTextTranslator } from "@/i18n/textTranslator";
import { useMeasures } from "@/i18n/measures";
import { homeReads } from "@/lib/api";
import { sourcesAsOf } from "@/lib/home/asOf";
import { useFamilyTiming } from "@/widgets/cadence";
import { healthDistribution, healthSummaryText } from "@/lib/behaviorOverview";
import { healthCategories } from "@/lib/home/healthCategories";
import { inventoryHonesty } from "@/lib/home/inventoryHonesty";
import { logSources } from "@/lib/home/logSources";

import { secondary, WidgetFrame, WidgetLoading, WidgetQualifiers } from "./WidgetFrame";
import { Unmeasured } from "./WidgetParts";

/** Which read stands behind a category, so its missing count names that read. */
const GAP_READ = {
  silent: "summary",
  exceeding: "summary",
  new: "fleet",
  conflict: "fleet",
  unclassified: "fleet",
  maintenance: "overview",
} as const;

export function LogSourceHealth() {
  const t = useTranslations("home.health");
  const say = asTextTranslator(useTranslations());
  const measures = useMeasures();
  const behaviorRead = homeReads.sourceBehaviors();
  const summaryRead = homeReads.anomalySummary();
  const fleetRead = homeReads.fleetSummary("24h");
  const overviewRead = homeReads.overview();
  const behaviors = useApiResource(behaviorRead.key, behaviorRead.fetch);
  const summary = useApiResource(summaryRead.key, summaryRead.fetch);
  const fleet = useApiResource(fleetRead.key, fleetRead.fetch);
  const overview = useApiResource(overviewRead.key, overviewRead.fetch);
  const { bucketSeconds } = useFamilyTiming("PHASE_A_VOLUME");
  const honesty =
    behaviors.status === "ok" ? inventoryHonesty(behaviors.data.length, overview) : "show";
  const frameState =
    honesty === "wait"
      ? ({ status: "loading" } as const)
      : honesty === "failed"
        ? { kind: "error" as const, action: t("inventoryFailed") }
        : honesty === "not_collected"
          ? { kind: "not_collected" as const, reason: t("inventoryMissing") }
          : behaviors;

  return (
    <WidgetFrame
      id="log-source-health"
      title={t("title")}
      windowText={t("latestState")}
      link={{ href: "/behavior/sources", label: t("link") }}
      state={frameState}
      dataTo={{
        at: behaviors.status === "ok" ? sourcesAsOf(behaviors.data, bucketSeconds) : null,
        family: "PHASE_A_VOLUME",
      }}
      what={t("what")}
      permission="read:anomalies"
    >
      {({ data: sources }) => {
          const sum = secondary(summary, say);
          const fl = secondary(fleet, say);
          const ov = secondary(overview, say);
          if (sum.pending || fl.pending || ov.pending) {
            return <WidgetLoading what={t("loadingWhat")} />;
          }
          const card = sum.data
            ? logSources(sum.data, sources, ov.data ? ov.data.log_sources.total_log_sources : null)
            : null;
          const groups = healthDistribution(sources);
          const categories = healthCategories({ summary: sum.data, fleet: fl.data, overview: ov.data });
          return (
            <>
              {card?.judgedText ? (
                <p className="tw-lead">
                  <span className="num">
                    {t("judgedNormal", {
                      normal: measures.count(card.normal),
                      judged: measures.count(card.judged),
                    })}
                  </span>
                  {card.notJudged > 0 && (
                    <span className="muted">
                      {" · "}
                      {t("notJudged", { count: measures.count(card.notJudged) })}
                    </span>
                  )}
                </p>
              ) : null}
              {sources.length === 0 ? (
                <p className="tw-state">{t("noSources")}</p>
              ) : (
                <HealthDistribution
                  groups={groups}
                  total={sources.length}
                  summary={healthSummaryText(
                    groups,
                    sources.length,
                    sum.data ? sum.data.silent_sources : 0,
                    say,
                  )}
                  say={say}
                />
              )}
              {card && sources.length > 0 && (
                <WidgetQualifiers
                  qualifiers={card.qualifiers}
                  extra={card.evaluationFault ?? undefined}
                  extraTitle={card.evaluationFaultDetail ?? undefined}
                />
              )}
              {sum.data === null && (
                <p className="tw-note">{t("noSummary", { gap: sum.gap ?? "" })}</p>
              )}
              <TableScroll label={t("categories")}>
                <table className="tw-table tw-categories" aria-label={t("categories")}>
                  <colgroup>
                    <col />
                    <col className="tw-col-count" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col">{t("category")}</th>
                      <th scope="col" className="num">
                        {t("count")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {categories.map((c) => (
                      <tr key={c.key}>
                        <td className="tw-cat-label" title={t(`categoryDefinition.${c.key}`)}>
                          {c.href !== null && c.count !== null ? (
                            <Link className="tw-link" href={c.href}>
                              {t(`categoryLabel.${c.key}`)}
                            </Link>
                          ) : (
                            t(`categoryLabel.${c.key}`)
                          )}
                        </td>
                        <td className="num">
                          {c.count === null ? (
                            <Unmeasured reason={c.gap ? t(`gap.${GAP_READ[c.key]}`) : null} />
                          ) : (
                            measures.count(c.count)
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
            </>
          );
      }}
    </WidgetFrame>
  );
}
