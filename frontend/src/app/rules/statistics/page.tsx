import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";

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
  formatCount,
} from "@/components/rules/ruleStatistics";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { ApiError, ruleStatisticsApi, type RuleStatistics } from "@/lib/api";
import type { Locale } from "@/i18n/locale";
import { withRouteMessages } from "@/i18n/RouteMessages";

export async function generateMetadata() {
  const t = await getTranslations("rules.meta");
  return { title: t("statistics") };
}

async function RuleStatisticsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const days = parseDays(params.days);
  const rank = parseRank(params.rank);
  const t = await getTranslations("rules.statistics");
  const locale = (await getLocale()) as Locale;
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
    <nav className="range-selector stats-days" aria-label={t("daysLabel")}>
      <span className="muted">{t("window")}</span>
      {DAY_CHOICES.map((choice) => (
        <Link
          key={choice}
          href={statsHref({ ...query, days: choice })}
          className={`range-option${choice === days ? " range-option-active" : ""}`}
          aria-current={choice === days ? "page" : undefined}
        >
          {formatCount(choice, locale)} {choice === 1 ? t("day") : t("days")}
        </Link>
      ))}
    </nav>
  );

  return (
    <>
      <PageHeader
        guide={guideFor("/rules/statistics")}
        title={t("title")}
        description={t("description")}
        actions={daySelector}
      />

      {noInstance ? (
        <div className="notice" role="status">
          <p>
            <strong>{t("noInstanceTitle")}</strong>
          </p>
          <p>
            {t("noInstanceHelp")}
          </p>
        </div>
      ) : error || !statistics ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("failedTitle")}</strong>
          </p>
          <p>
            {t("failedHelp")}
          </p>
        </div>
      ) : (
        <>
          <MeasurementWindow window={statistics.window} />
          <RuleComposition counts={statistics.counts} query={query} />
          <MostMatched
            rows={rank === "events" ? statistics.top_by_events : statistics.top_by_matches}
            reason={statistics.top_reason}
            reasonCode={statistics.top_reason_code}
            reasonParams={statistics.top_reason_params}
            query={query}
          />
          <SilentRules
            silent={statistics.silent}
            reason={statistics.silent_reason}
            reasonCode={statistics.silent_reason_code}
            reasonParams={statistics.silent_reason_params}
          />
          <CostliestRules cost={statistics.costliest_by_benchmark} />
        </>
      )}
    </>
  );
}

export default withRouteMessages("/rules/statistics", RuleStatisticsPage);
