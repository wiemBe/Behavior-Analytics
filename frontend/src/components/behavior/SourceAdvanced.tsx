// The source page's technical detail, all of it collapsed.
//
// Four disclosures replacing what used to be a long scroll of tables. At most
// one is open at a time in practice, and none is open on load: an analyst
// asking "is this source healthy?" should never have to scroll past the
// baseline cell history to reach the answer.
//
// Nothing is removed. Every figure the previous page rendered is still here,
// one interaction away.

import { useTranslations } from "next-intl";

import { Disclosure } from "@/components/ui/Disclosure";
import { TableScroll } from "@/components/ui/TableScroll";
import type {
  BaselineCell,
  LogSourceDetail,
  MetricBucket,
  SourceBehavior,
} from "@/lib/api";
import { completenessMeaning, formatMetric } from "@/lib/behavior";
import { clearedFaultLine } from "@/lib/evaluationFault";
import { UtcTime } from "@/components/ui/UtcTime";
import {
  MAD_EPS,
  P05_P95_EPS,
  completenessCode,
  errorClassText,
  versionLabel,
} from "@/lib/behaviorI18n";
import { plainCount } from "@/lib/figureText";
import { formatCount, formatShare } from "@/lib/format";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { OriginMarker } from "./HistoryKey";

/** Buckets shown in the table. The chart covers the whole window. */
const BUCKET_ROWS = 60;

export function SourceAdvanced({
  behavior,
  meta,
  buckets,
  cells,
  cellsFailed,
  bucketsFailed = false,
}: {
  behavior: SourceBehavior;
  meta: LogSourceDetail | null;
  buckets: MetricBucket[];
  cells: BaselineCell[];
  cellsFailed: boolean;
  /** The metric read failed: the bucket table is not "empty", it is unread. */
  bucketsFailed?: boolean;
}) {
  const t = useTranslations("behavior.advanced");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  const metric = (value: number | null | undefined, digits = 2) => formatMetric(value, digits, locale);
  const band = (low: number | null, high: number | null) => `${metric(low)} – ${metric(high)}`;
  const yesNo = (value: boolean) => (value ? t("yes") : t("no"));
  const recent = [...buckets]
    .sort((a, b) => Date.parse(b.bucket_start) - Date.parse(a.bucket_start))
    .slice(0, BUCKET_ROWS);
  const cleared = clearedFaultLine(behavior);

  return (
    <div className="stack-6">
      {cleared && (
        <p className="subtitle">
          {t.rich(cleared.lastFailure ? "clearedWithTime" : "clearedNoTime", {
            bucket: () => <UtcTime value={cleared.failedBucket} variant="compact" />,
            failed: t(
              cleared.attempts == null
                ? "clearedFailed.unknown"
                : cleared.attempts === 1
                  ? "clearedFailed.once"
                  : "clearedFailed.times",
              { count: plainCount(cleared.attempts ?? 0, locale) },
            ),
            errorClass: errorClassText(cleared.errorClass),
            time: () =>
              cleared.lastFailure ? <UtcTime value={cleared.lastFailure} variant="compact" /> : null,
            resolved: () => <UtcTime value={cleared.resolvedAt} variant="compact" />,
          })}
        </p>
      )}
      <Disclosure
        summary={t("baselineHistory")}
        note={cellsFailed ? t("notLoaded") : t("cellsNote", { count: plainCount(cells.length, locale) })}
      >
        {cellsFailed ? (
          <div className="notice" role="alert">
            {t("cellsFailed")}
          </div>
        ) : cells.length === 0 ? (
          <div className="notice">
            {t("cellsEmpty")}
          </div>
        ) : (
          <TableScroll label={t("cellsLabel")}>
            <table>
              <caption className="sr-only">
                {t("cellsCaption")}
              </caption>
              <thead>
                <tr>
                  <th scope="col" className="num">
                    {t("weekday")}
                  </th>
                  <th scope="col" className="num">
                    {t("hour")}
                  </th>
                  <th scope="col" className="num">
                    {t("median")}
                  </th>
                  <th scope="col" className="num">
                    {MAD_EPS}
                  </th>
                  <th scope="col" className="num">
                    {P05_P95_EPS}
                  </th>
                  <th scope="col" className="num">
                    {t("samples")}
                  </th>
                  <th scope="col" className="num">
                    {t("completeness")}
                  </th>
                  <th scope="col">{t("reliable")}</th>
                  <th scope="col" className="num">
                    {t("version")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {cells.map((c) => (
                  <tr key={`${c.metric_name}-${c.weekday}-${c.hour}`}>
                    <td className="num">{c.weekday}</td>
                    <td className="num">{c.hour}</td>
                    <td className="num">{metric(c.median)}</td>
                    <td className="num">{metric(c.mad, 3)}</td>
                    <td className="num">
                      {band(c.p05, c.p95)}
                    </td>
                    <td className="num">{formatCount(c.sample_count, locale)}</td>
                    <td className="num">{formatShare(c.completeness, locale)}</td>
                    <td>
                      {/* Stated as a word, not only as a colour. */}
                      <span className={`pill pill-quiet ${c.is_reliable ? "ok" : "warn"}`}>
                        {yesNo(c.is_reliable)}
                      </span>
                    </td>
                    <td className="num">{versionLabel(c.baseline_version)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </Disclosure>

      <Disclosure
        summary={t("metricBuckets")}
        note={
          bucketsFailed
            ? t("notLoaded")
            : t("mostRecent", { count: plainCount(Math.min(recent.length, BUCKET_ROWS), locale) })
        }
      >
        {bucketsFailed ? (
          <div className="notice">
            {t("bucketsFailed")}
          </div>
        ) : recent.length === 0 ? (
          <div className="notice">
            {t("bucketsEmpty")}
          </div>
        ) : (
          <TableScroll label={t("metricBuckets")}>
            <table>
              <caption className="sr-only">
                {t("bucketsCaption")}
              </caption>
              <thead>
                <tr>
                  <th scope="col" className="num">
                    {t("interval")}
                  </th>
                  <th scope="col" className="num">
                    {t("events")}
                  </th>
                  <th scope="col" className="num">
                    {t("averageEps")}
                  </th>
                  <th scope="col" className="num">
                    {t("peakEps")}
                  </th>
                  <th scope="col">{t("completeness")}</th>
                  <th scope="col" className="num">
                    {t("lastEvent")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {recent.map((b) => (
                  <tr key={b.bucket_start}>
                    <td className="num">
                      <UtcTime value={b.bucket_start} />
                    </td>
                    <td className="num">{formatCount(b.event_count, locale)}</td>
                    {/* A COMPLETE zero is a measurement and renders as 0.00.
                        Only an unobserved interval renders as an em dash. */}
                    <td className="num">
                      {b.completeness === "COMPLETE" ? (
                        metric(b.average_eps)
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="num">{metric(b.peak_eps)}</td>
                    <td>
                      <span
                        className={`pill pill-quiet ${b.completeness === "COMPLETE" ? "ok" : "warn"}`}
                        title={completenessMeaning(b.completeness, i18n)}
                      >
                        {completenessCode(i18n, b.completeness)}
                      </span>
                      <OriginMarker origin={b.origin} />
                    </td>
                    <td className="num">{<UtcTime value={b.last_event_at} />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </Disclosure>

      <Disclosure summary={t("collectionDetails")}>
        <dl className="dimension-meta">
          {behavior.skipped_buckets && behavior.skipped_buckets.length > 0 && (
            <div>
              <dt>{t("bucketsNotJudged")}</dt>
              <dd>
                {behavior.skipped_buckets.map((b) => (
                  <span key={b.bucket_start} className="cell-sub">
                    <UtcTime value={b.bucket_start} variant="compact" /> ({b.error_class})
                  </span>
                ))}
              </dd>
            </div>
          )}
          <div>
            <dt>{t("lastCollected")}</dt>
            <dd className="num">{<UtcTime value={behavior.last_bucket_at} />}</dd>
          </div>
          <div>
            <dt>{t("lastEvent")}</dt>
            <dd className="num">{<UtcTime value={behavior.last_event_at} />}</dd>
          </div>
          <div>
            <dt>{t("baselineSamples")}</dt>
            <dd className="num">{formatCount(behavior.baseline_sample_count, locale)}</dd>
          </div>
          <div>
            <dt>{t("baselineCompleteness")}</dt>
            <dd className="num">
              {formatShare(behavior.baseline_completeness, locale)}
            </dd>
          </div>
          <div>
            <dt>{t("collectionSource")}</dt>
            <dd>{t("collectionSourceValue")}</dd>
          </div>
        </dl>
      </Disclosure>

      <Disclosure summary={t("sourceMetadata")}>
        {meta ? (
          <dl className="dimension-meta">
            <div>
              <dt>{t("qradarId")}</dt>
              <dd className="num">{meta.qradar_id}</dd>
            </div>
            <div>
              <dt>{t("type")}</dt>
              <dd>{meta.type_name ?? <span className="muted">—</span>}</dd>
            </div>
            <div>
              <dt>{t("owner")}</dt>
              <dd>{meta.owner ?? <span className="muted">—</span>}</dd>
            </div>
            <div>
              <dt>{t("enabled")}</dt>
              <dd>{yesNo(meta.enabled)}</dd>
            </div>
            <div>
              <dt>{t("monitoring")}</dt>
              <dd>{meta.monitoring_enabled ? t("monitoringOn") : t("monitoringOff")}</dd>
            </div>
            <div>
              <dt>{t("maintenance")}</dt>
              <dd>{meta.maintenance_mode ? t("maintenanceOn") : t("maintenanceOff")}</dd>
            </div>
            <div>
              <dt>{t("qradarStatus")}</dt>
              <dd>{meta.qradar_status ?? <span className="muted">—</span>}</dd>
            </div>
            <div>
              <dt>{t("timezone")}</dt>
              <dd>{meta.timezone_name ?? <span className="muted">—</span>}</dd>
            </div>
          </dl>
        ) : (
          <div className="notice">
            {t("metadataFailed")}
          </div>
        )}
      </Disclosure>
    </div>
  );
}
