"use client";

// Log source health (UR.4.3): the fleet's states as `HealthDistribution`, then the
// categories that have real detection logic, each with its count and, where a page
// owns the list, a link to it. There is no "Degraded": nothing detects it.
//
// The figures come from four reads. The states are the widget's own question and
// gate it; each category row stands on its read alone, and a read that failed
// leaves that row at a dash with the reason, never at zero.

import Link from "next/link";

import { HealthDistribution } from "@/components/behavior/HealthDistribution";
import { TableScroll } from "@/components/ui/TableScroll";
import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { healthDistribution, healthSummaryText } from "@/lib/behaviorOverview";
import { healthCategories } from "@/lib/home/healthCategories";
import { logSources } from "@/lib/home/logSources";

import { Gate, QualifierList, secondary, Unmeasured, Widget, WidgetLoading } from "./WidgetParts";

export function LogSourceHealth() {
  const behaviorRead = homeReads.sourceBehaviors();
  const summaryRead = homeReads.anomalySummary();
  const fleetRead = homeReads.fleetSummary("24h");
  const overviewRead = homeReads.overview();
  const behaviors = useApiResource(behaviorRead.key, behaviorRead.fetch);
  const summary = useApiResource(summaryRead.key, summaryRead.fetch);
  const fleet = useApiResource(fleetRead.key, fleetRead.fetch);
  const overview = useApiResource(overviewRead.key, overviewRead.fetch);

  return (
    <Widget
      id="log-source-health"
      title="Log source health"
      windowText="latest state"
      viewAll={{ href: "/behavior/sources", label: "View all" }}
    >
      <Gate state={behaviors} what="the source states" permission="read:anomalies">
        {({ data: sources }) => {
          const sum = secondary(summary);
          const fl = secondary(fleet);
          const ov = secondary(overview);
          if (sum.pending || fl.pending || ov.pending) {
            return <WidgetLoading what="log source health" />;
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
                  <span className="num">{card.judgedText}</span>
                  {card.notJudged > 0 && (
                    <span className="muted">
                      {" · "}
                      {card.notJudged.toLocaleString("en-US")} not judged
                    </span>
                  )}
                </p>
              ) : null}
              {sources.length === 0 ? (
                <p className="tw-state">
                  No monitored log sources. Enable monitoring on a source to start collecting its
                  volume baseline.
                </p>
              ) : (
                <HealthDistribution
                  groups={groups}
                  total={sources.length}
                  summary={healthSummaryText(
                    groups,
                    sources.length,
                    sum.data ? sum.data.silent_sources : 0,
                  )}
                />
              )}
              {card && sources.length > 0 && <QualifierList qualifiers={card.qualifiers} />}
              {sum.data === null && (
                <p className="tw-note">
                  How many sources are judged normal is unavailable: the anomaly summary is not
                  readable ({sum.gap}).
                </p>
              )}
              <TableScroll label="Log source health categories">
                <table className="tw-table tw-categories">
                  <colgroup>
                    <col />
                    <col className="tw-col-count" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col">Category</th>
                      <th scope="col" className="num">
                        Count
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {categories.map((c) => (
                      <tr key={c.key}>
                        <td className="tw-cat-label" title={c.definition}>
                          {c.href !== null && c.count !== null ? (
                            <Link className="tw-link" href={c.href}>
                              {c.label}
                            </Link>
                          ) : (
                            c.label
                          )}
                        </td>
                        <td className="num">
                          {c.count === null ? <Unmeasured reason={c.gap} /> : c.count.toLocaleString("en-US")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
            </>
          );
        }}
      </Gate>
    </Widget>
  );
}
