// One IPS source: what is it doing now against its normal, and which of its entities are tracked
// and judged?
//
// The first screen is the source's own series against its expected range; the entity inventory
// follows. Every figure that was not measured says so: a bucket with no row is a gap, an action
// split that was never collected is "not mapped", and an entity no detector has evaluated is "not
// evaluated", never NORMAL.

import type { Metadata } from "next";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { Pagination } from "@/components/Pagination";
import { IpsEntitiesTable } from "@/components/ips/IpsEntitiesTable";
import {
  IpsDimensionFilter,
  IpsRangeSelector,
  IpsSeriesSelector,
  parseEntityDimension,
  parseIpsRange,
  type IpsSourceQuery,
} from "@/components/ips/IpsSourceControls";
import { IpsSeriesChart } from "@/components/ips/IpsSeriesChart";
import { ActiveAnomaliesLine } from "@/components/ips/IpsActiveAnomalies";
import { ipsSeriesCounts } from "@/components/ips/ipsSeries";
import { ipsValidationView, isJudged } from "@/components/ips/ipsSourceHealth";
import {
  SOURCE_SERIES_SPECS,
  actionMix,
  hasReliableCell,
  parseSeries,
  seriesPoints,
  sourceChartSummary,
} from "@/components/ips/ipsSourceSeries";
import { TableScroll } from "@/components/ui/TableScroll";
import { PageHeader } from "@/components/ui/PageHeader";
import { UtcTime } from "@/components/ui/UtcTime";
import { renderBackendMessage } from "@/i18n/backendMessage";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { asTextTranslator } from "@/i18n/textTranslator";
import {
  ApiError,
  api,
  rateLimitedText,
  type IpsBaselineCell,
  type IpsSourceBehavior,
  type PagedIpsEntities,
  type PagedIpsSourceMetrics,
} from "@/lib/api";
import { formatCount } from "@/lib/behavior";
import { refusalSentences } from "@/lib/refusal";
import { getBehaviorI18n } from "@/lib/behaviorI18n.server";
import { formatPercent } from "@/lib/format";
import { guideFor } from "@/lib/guideRoutes";
import { trafficHref, parseTrafficQuery } from "@/lib/traffic";

import "@/components/ips/ips.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("ips.bsource");
  return { title: t("metaTitle") };
}

const PAGE_SIZE = 50;

/** A server sentence is written without a final stop; the link after it needs one. */
const sentenceEnd = (text: string) => (/[.!?]$/.test(text) ? text : `${text}.`);
// The metrics route caps a page at 5000 buckets; a range that needs more says so.
const METRICS_LIMIT = 5000;

function Problem({ title, text, pageTitle }: { title: string; text: React.ReactNode; pageTitle: string }) {
  return (
    <>
      <PageHeader guide={guideFor("/behavior/ips/sources/[id]")} title={pageTitle} />
      <div className="notice" role="alert">
        <p>
          <strong>{title}</strong>
        </p>
        <p>{text}</p>
      </div>
    </>
  );
}

async function IpsSourcePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const t = await getTranslations("ips.bsource");
  const i18n = await getBehaviorI18n();
  const { locale } = i18n;
  const say = asTextTranslator(await getTranslations());
  const { id } = await params;
  const raw = await searchParams;
  const range = parseIpsRange(raw.range);
  const query: IpsSourceQuery = {
    range: range.key,
    series: parseSeries(raw.series),
    dimension: parseEntityDimension(raw.dimension),
  };
  const spec = SOURCE_SERIES_SPECS[query.series];
  const parsedOffset = Number(raw.offset ?? 0);
  const offset = Number.isFinite(parsedOffset) && parsedOffset > 0 ? Math.floor(parsedOffset) : 0;

  const until = new Date();
  const since = new Date(until.getTime() - range.hours * 3600_000);

  // The server's coded 404 (`ips.source.not_found`) leads. It is sent for an unknown id, for a
  // source without an enabled IPS profile and for another console's source, so the page's own
  // sentence says those cases and the next step instead of claiming the source does not exist.
  const notFound = (err: ApiError) => {
    const sentence = refusalSentences(err)[0];
    return (
      <Problem
        pageTitle={t("title")}
        title={sentence ? sentenceEnd(renderBackendMessage(sentence, locale, say)) : t("notFoundTitle")}
        text={
          <>
            {t("notFoundText")} <Link href="/behavior/ips">{t("openBehavior")}</Link>
          </>
        }
      />
    );
  };

  let source: IpsSourceBehavior;
  let entities: PagedIpsEntities;
  let metrics: PagedIpsSourceMetrics | null;
  let cells: IpsBaselineCell[] | null = null;
  let baselinesFailed = false;
  try {
    [source, entities, metrics] = await Promise.all([
      api.ipsBehaviorSource(id),
      api.ipsBehaviorEntities(id, { dimension: query.dimension, limit: PAGE_SIZE, offset }),
      // The chart is a section of the page: its failure is said there, not for the whole page.
      api
        .ipsSourceMetrics(id, {
          since: since.toISOString(),
          until: until.toISOString(),
          limit: METRICS_LIMIT,
        })
        .catch(() => null),
    ]);
    if (spec.metric) {
      try {
        cells = (await api.ipsSourceBaselines(id, { metric: spec.metric, limit: 200 })).items;
      } catch {
        baselinesFailed = true;
      }
    }
  } catch (err) {
    if (err instanceof ApiError && err.status === 429) {
      return (
        <Problem
          pageTitle={t("title")}
          title={t("failedTitle")}
          text={rateLimitedText(err, asTextTranslator(await getTranslations()))}
        />
      );
    }
    if (err instanceof ApiError && err.status === 404) {
      return notFound(err);
    }
    if (err instanceof ApiError && err.status === 403) {
      return <Problem pageTitle={t("title")} title={t("forbiddenTitle")} text={t("forbiddenText")} />;
    }
    if (err instanceof ApiError && err.status === 401) {
      return <Problem pageTitle={t("title")} title={t("expiredTitle")} text={t("expiredText")} />;
    }
    return <Problem pageTitle={t("title")} title={t("failedTitle")} text={t("failedText")} />;
  }

  const v = ipsValidationView(source, i18n);
  const judged = isJudged(source);
  const latest = source.latest_bucket;
  const buckets = metrics?.items ?? [];
  const points = seriesPoints(query.series, buckets);
  const counts = ipsSeriesCounts(points);
  const quantity = t(`series.quantity.${query.series}`);
  const bucketSeconds = buckets[0]?.bucket_seconds ?? null;
  const reliable = spec.metric != null && cells != null && hasReliableCell(cells, spec.metric);
  const baseline =
    spec.metric && cells && reliable ? { cells, metric: spec.metric, pooled: spec.pooled } : null;
  const mix = actionMix(buckets);
  const drawn = metrics ? metrics.items.length : 0;
  const trafficLink = trafficHref({ ...parseTrafficQuery({}), view: "ips", source: id });
  const chartBanner = (word: string, body: React.ReactNode) => (
    <p className="banner warn">
      <span>
        <strong>{word}.</strong> {body}
      </span>
    </p>
  );

  return (
    <>
      <PageHeader
        guide={guideFor("/behavior/ips/sources/[id]")}
        title={source.log_source_name}
        description={t("description")}
        meta={
          <>
            {source.product_label && <span>{source.product_label}</span>}
            {source.product_label && (
              <span className="muted" aria-hidden="true">
                ·
              </span>
            )}
            <span className={`pill pill-strong ${v.tone}`}>{v.label}</span>
            <span>{v.detail}</span>
            <span className="muted" aria-hidden="true">
              ·
            </span>
            <span>{source.enabled ? i18n.t("ips.behavior.profileEnabled") : i18n.t("ips.behavior.profileDisabled")}</span>
            <span className="muted" aria-hidden="true">
              ·
            </span>
            {latest ? (
              <span>
                {t("latestBucket")} <UtcTime value={latest.bucket_start} />
              </span>
            ) : (
              <span>{t("noBucketYet")}</span>
            )}
          </>
        }
        actions={
          <>
            <Link href="/behavior/ips" className="action secondary button-link">
              {t("allSources")}
            </Link>
            <Link href={`/admin/ips-sources/${id}`} className="action secondary button-link">
              {t("profileSettings")}
            </Link>
            <Link href={trafficLink} className="action secondary button-link">
              {t("openTraffic")}
            </Link>
          </>
        }
      />

      <ActiveAnomaliesLine s={source} />

      {!judged && <p className="banner warn">{t("notCollectedBanner")}</p>}

      <section className="stack-6">
        <h2 className="section-title">{t("series.heading")}</h2>
        <div className="ips-controls">
          <IpsRangeSelector id={id} query={query} />
          <IpsSeriesSelector id={id} query={query} />
        </div>
        {metrics == null ? (
          <div className="notice" role="alert">
            {t("chartFailed")}
          </div>
        ) : points.length === 0 ? (
          <div className="notice" role="status">
            {t("noBuckets")}
          </div>
        ) : (
          <>
            <p className="subtitle">
              {t.rich("covers", {
                start: () => <UtcTime value={metrics.range_start} />,
                end: () => <UtcTime value={metrics.range_end} />,
              })}
            </p>
            {metrics.total > drawn &&
              chartBanner(
                i18n.t("ips.banner.truncated"),
                t("truncatedRange", {
                  shown: formatCount(drawn, locale),
                  total: formatCount(metrics.total, locale),
                }),
              )}
            {counts.notExact > 0 &&
              chartBanner(
                i18n.t("ips.banner.partial"),
                i18n.t("ips.banner.partialBody", {
                  notExact: formatCount(counts.notExact, locale),
                  stored: formatCount(counts.stored, locale),
                  extra:
                    (counts.notMeasured > 0
                      ? i18n.t("ips.banner.partialNotMeasured", {
                          count: formatCount(counts.notMeasured, locale),
                        })
                      : "") +
                    (counts.lowerBound > 0
                      ? i18n.t("ips.banner.partialLowerBound", {
                          count: formatCount(counts.lowerBound, locale),
                        })
                      : ""),
                }),
              )}
            {counts.maintenance > 0 && (
              <p className="subtitle">{i18n.t("ips.banner.maintenanceBody", { count: counts.maintenance })}</p>
            )}
            {spec.metric == null && <p className="subtitle">{t("noBaselineEvents")}</p>}
            {spec.metric != null && baselinesFailed && chartBanner(
              i18n.t("ips.banner.baselineUnavailable"),
              t("baselineFailed"),
            )}
            {spec.metric != null && cells != null && !reliable &&
              chartBanner(
                i18n.t("enums.anomalyState.INSUFFICIENT_DATA"),
                t("noReliableCell"),
              )}
            <IpsSeriesChart
              points={points}
              rows={buckets}
              unit={spec.unit}
              quantity={quantity}
              bucketSeconds={bucketSeconds}
              baseline={baseline}
              expected={null}
              expectedLow={null}
              expectedHigh={null}
              ariaLabel={t("chartAria", { quantity })}
              textSummary={sourceChartSummary(points, quantity, source.log_source_name, i18n)}
            />
          </>
        )}
      </section>

      <section className="stack-6 ips-section">
        <h2 className="section-title">{t("action.heading")}</h2>
        {metrics == null ? (
          <p className="subtitle">{t("chartFailed")}</p>
        ) : mix == null ? (
          <p className="subtitle">{buckets.length === 0 ? t("noBuckets") : t("action.notMapped")}</p>
        ) : (
          <>
            <TableScroll label={t("action.caption")}>
              <table className="table-dense ips-actions">
                <caption className="sr-only">{t("action.caption")}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t("action.action")}</th>
                    <th scope="col" className="num">
                      {t("action.events")}
                    </th>
                    <th scope="col" className="num">
                      {t("action.share")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {(["blocked", "allowed", "unmapped"] as const).map((k) => {
                    const total = mix.blocked + mix.allowed + mix.unmapped;
                    return (
                      <tr key={k}>
                        <td>{t(`action.${k}`)}</td>
                        <td className="num">
                          {mix.lowerBound ? "≥ " : ""}
                          {formatCount(mix[k], locale)}
                        </td>
                        <td className="num">
                          {total > 0 ? formatPercent(mix[k] / total, mix.lowerBound ? "LOWER_BOUND" : "EXACT", 1, locale) : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableScroll>
            <p className="subtitle">
              {t("action.basis", {
                measured: formatCount(mix.measured, locale),
                stored: formatCount(mix.stored, locale),
              })}
              {mix.lowerBound ? ` ${t("action.lower")}` : ""}
            </p>
          </>
        )}
      </section>

      <section className="stack-6 ips-section">
        <h2 className="section-title">{t("entities.heading")}</h2>
        <IpsDimensionFilter id={id} query={query} />
        {entities.items.length === 0 ? (
          <div className="notice" role="status">
            <p>
              <strong>
                {query.dimension ? t("entities.emptyDimensionTitle") : t("entities.emptyTitle")}
              </strong>
            </p>
            <p>{judged ? t("entities.emptyCollected") : t("entities.emptyNotCollected")}</p>
          </div>
        ) : (
          <>
            <div className="ips-entities">
              <IpsEntitiesTable entities={entities.items} />
            </div>
            <Pagination
              total={entities.total}
              limit={PAGE_SIZE}
              offset={offset}
              basePath={`/behavior/ips/sources/${id}`}
              params={{
                range: query.range === "24h" ? undefined : query.range,
                series: query.series === "events" ? undefined : query.series,
                dimension: query.dimension,
              }}
            />
          </>
        )}
      </section>
    </>
  );
}

export default withRouteMessages("/behavior/ips/sources/[id]", IpsSourcePage);
