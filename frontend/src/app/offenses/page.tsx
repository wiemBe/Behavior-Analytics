import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { api, type OffenseAggregates, type OffenseSummary, type Page } from "@/lib/api";
import { loadErrorMessage } from "@/lib/loadError";
import { StatCard } from "@/components/StatCard";
import { formatCount } from "@/lib/format";
import { formatDuration } from "@/lib/behavior";
import { magnitudeTone, offenseStatusTone } from "@/lib/health";
import { Pagination } from "@/components/Pagination";
import { OffenseFilters } from "@/components/OffenseFilters";
import { ReportDataAge } from "@/components/shell/ReportDataAge";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import type { Locale } from "@/i18n/locale";
import { withRouteMessages } from "@/i18n/RouteMessages";

import "@/components/ui/controls.css";

import "./offenses.css";

const PAGE_SIZE = 25;

export async function generateMetadata() {
  const t = await getTranslations("offenses.meta");
  return { title: t("list") };
}

/** A nullable open count. Null is an em dash; the group states why once. A measured 0 stays 0. */
function offenseStat(
  label: string,
  count: number | null,
  alarm?: "crit" | "warn",
  unknown?: { count: number | null | undefined; what: string },
  locale: Locale = "en",
  unknownText?: (count: number, what: string) => string,
) {
  if (count == null) return <StatCard label={label} value="—" />;
  // Open offenses the count could not classify: said beside the figure, in words.
  const note =
    unknown && typeof unknown.count === "number" && unknown.count > 0
      ? unknownText?.(unknown.count, unknown.what) ??
        `+\u00a0${formatCount(unknown.count)}\u00a0with unknown ${unknown.what}`
      : undefined;
  return (
    <StatCard
      label={label}
      value={formatCount(count, locale)}
      tone={alarm && count > 0 ? alarm : undefined}
      note={note}
    />
  );
}

/** Magnitude is 0 to 10; the bar is drawn in whole tenths of the track. */
function magnitudeStep(magnitude: number): number {
  return Math.min(10, Math.max(0, Math.round(magnitude)));
}

async function OffensesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const offset = Math.max(0, Number(params.offset ?? 0) || 0);
  const search = params.search ?? "";
  const status = params.status ?? "";
  const t = await getTranslations("offenses");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as Locale;

  let page: Page<OffenseSummary> | null = null;
  let aggregates: OffenseAggregates | null = null;
  let error: string | null = null;

  try {
    // Fetched together: the counters describe the whole instance, not the
    // current page, so they must not be derived from `items`.
    [page, aggregates] = await Promise.all([
      api.offenses({ limit: PAGE_SIZE, offset, search, status }),
      api.offenseAggregates(),
    ]);
  } catch (err) {
    error = loadErrorMessage(err, t("loadNoun"), "read:offenses", say);
  }

  if (error) {
    return (
      <>
        <PageHeader guide={guideFor("/offenses")} title={t("title")} />
        <div className="notice" role="alert">
          <p>{error}</p>
        </div>
      </>
    );
  }

  const items = page?.items ?? [];

  return (
    <>
      {/* Neither read says how far the offense snapshots reach: the page has no data time. */}
      <ReportDataAge iso={null} />
      <PageHeader
        guide={guideFor("/offenses")}
        title={t("title")}
        description={t("description")}
      />

      {aggregates && (
        <>
          <div className="cards offenses-matching">
            {/* `active` is null when no offense snapshot exists. The list total is 0 in that
                case too, and a measured empty filter is also 0, so the snapshot is the signal. */}
            <StatCard
              label={t("matching")}
              value={
                aggregates.active == null ? "—" : formatCount(page?.total ?? 0, locale)
              }
            />
          </div>
          <h2>{t("openWhole")}</h2>
          {(aggregates.active == null ||
            aggregates.critical == null ||
            aggregates.unassigned == null ||
            aggregates.exceeding_sla == null ||
            aggregates.oldest_age_seconds == null) && (
            <p className="notice" role="status">
              {t("notCollectedNotice", { reason: t("notCollected") })}
            </p>
          )}
          <div className="cards offenses-open">
            {offenseStat(t("stats.active"), aggregates.active, undefined, undefined, locale)}
            {offenseStat(
              t("stats.critical", { magnitude: aggregates.critical_magnitude }),
              aggregates.critical,
              "crit",
              { count: aggregates.critical_unknown, what: t("stats.magnitude") },
              locale,
              (count, what) => t("stats.unknown", { count, what }),
            )}
            {offenseStat(t("stats.unassigned"), aggregates.unassigned, "warn", undefined, locale)}
            {offenseStat(
              t("stats.overSla", { hours: aggregates.sla_hours }),
              aggregates.exceeding_sla,
              "warn",
              { count: aggregates.age_unknown, what: t("stats.age") },
              locale,
              (count, what) => t("stats.unknown", { count, what }),
            )}
            <StatCard label={t("stats.oldest")} value={formatDuration(aggregates.oldest_age_seconds, { t: say, locale })} />
          </div>
        </>
      )}

      <OffenseFilters search={search} status={status} />

      {items.length === 0 ? (
        <div className="notice">
          {search || status
            ? t("emptyFiltered")
            : t("empty")}
        </div>
      ) : (
        <>
          <TableScroll label={t("table.label")}>
          <table className="offenses-table table-dense" aria-label={t("table.label")}>
            <thead>
              <tr>
                <th>{t("table.id")}</th>
                <th>{t("table.description")}</th>
                <th title={t("table.magnitudeTitle")}>{t("table.magnitude")}</th>
                <th className="num" title={t("table.severityTitle")}>
                  {t("table.severity")}
                </th>
                <th>{t("table.status")}</th>
                <th>{t("table.assigned")}</th>
                <th className="num">{t("table.events")}</th>
                <th>{t("table.started")}</th>
                <th className="num">{t("table.age")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((o) => (
                <tr key={o.qradar_offense_id}>
                  <td>
                    <Link href={`/offenses/${o.qradar_offense_id}`}>{o.qradar_offense_id}</Link>
                  </td>
                  {/* Rendered as text, never as markup: this string comes from
                      QRadar. The backend sanitizes it; React escapes it again. */}
                  <td className="cell-description">{o.description?.trim() || "—"}</td>
                  <td className="cell-magnitude">
                    <span className={`pill ${magnitudeTone(o.magnitude)}`}>
                      {o.magnitude ?? "—"}
                    </span>
                    {o.magnitude != null && (
                      <span
                        className={`mag-bar ${magnitudeTone(o.magnitude)} mag-w-${magnitudeStep(o.magnitude)}`}
                        aria-hidden="true"
                      />
                    )}
                  </td>
                  <td className="num">{o.severity ?? "—"}</td>
                  <td>
                    <span className={`pill ${offenseStatusTone(o.status)}`}>
                      {enumLabel(say, "offenseStatus", o.status)}
                    </span>
                  </td>
                  <td>{o.assigned_to ?? <span className="muted">{t("table.unassigned")}</span>}</td>
                  <td className="num">
                    {o.event_count == null ? "—" : formatCount(o.event_count, locale)}
                  </td>
                  <td>{o.start_time ? <UtcTime value={o.start_time} wrapZone /> : "—"}</td>
                  <td className="num">{formatDuration(o.age_seconds, { t: say, locale })}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </TableScroll>

          <Pagination
            total={page?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            basePath="/offenses"
            params={{ search, status }}
          />
        </>
      )}
    </>
  );
}

export default withRouteMessages("/offenses", OffensesPage);
