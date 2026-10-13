import type { Metadata } from "next";
import Link from "next/link";

import { CostliestRules } from "@/components/rules/CostliestRules";
import { MeasurementWindow } from "@/components/rules/MeasurementWindow";
import { MostMatched } from "@/components/rules/MostMatched";
import { RuleComposition } from "@/components/rules/RuleComposition";
import { SilentRules } from "@/components/rules/SilentRules";
import {
  DAY_CHOICES,
  originsPresent,
  parseDays,
  parseOrigin,
  parseRank,
  statsHref,
  type StatsQuery,
} from "@/components/rules/ruleStatistics";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, ruleStatisticsApi, type RuleStatistics } from "@/lib/api";

export const metadata: Metadata = {
  title: "Rule statistics",
};

export default async function RuleStatisticsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const days = parseDays(params.days);
  const rank = parseRank(params.rank);
  let statistics: RuleStatistics | null = null;
  let noInstance = false;
  let error = false;

  try {
    statistics = await ruleStatisticsApi.statistics(days);
  } catch (err) {
    noInstance = err instanceof ApiError && err.status === 404;
    error = !noInstance;
  }

  const presentOrigins = statistics ? originsPresent(statistics.counts) : [];
  const query: StatsQuery = {
    days,
    rank,
    origin: parseOrigin(params.origin, presentOrigins),
  };

  const daySelector = (
    <nav className="range-selector stats-days" aria-label="Measurement days">
      <span className="muted">Window</span>
      {DAY_CHOICES.map((choice) => (
        <Link
          key={choice}
          href={statsHref({ ...query, days: choice })}
          className={`range-option${choice === days ? " range-option-active" : ""}`}
          aria-current={choice === days ? "page" : undefined}
        >
          {choice} {choice === 1 ? "day" : "days"}
        </Link>
      ))}
    </nav>
  );

  return (
    <>
      <PageHeader
        title="Rule statistics"
        description="Inventory composition, measured matching load, proven silence and QRadar evaluation benchmarks."
        actions={daySelector}
      />

      {noInstance ? (
        <div className="notice" role="status">
          <p>
            <strong>No QRadar instance is registered.</strong>
          </p>
          <p>
            Register the QRadar instance in the platform configuration, then run rule inventory
            and rule-match collection before returning to this page.
          </p>
        </div>
      ) : error || !statistics ? (
        <div className="notice" role="alert">
          <p>
            <strong>Rule statistics could not be loaded.</strong>
          </p>
          <p>
            The backend may be unreachable. Check that it is running, then reload this page;
            nothing was changed.
          </p>
        </div>
      ) : (
        <>
          <MeasurementWindow window={statistics.window} />
          <RuleComposition counts={statistics.counts} query={query} />
          <MostMatched
            rows={rank === "events" ? statistics.top_by_events : statistics.top_by_matches}
            reason={statistics.top_reason}
            query={query}
          />
          <SilentRules silent={statistics.silent} reason={statistics.silent_reason} />
          <CostliestRules cost={statistics.costliest_by_benchmark} />
        </>
      )}
    </>
  );
}
