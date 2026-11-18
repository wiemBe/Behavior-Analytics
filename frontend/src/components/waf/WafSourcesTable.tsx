import { useTranslations } from "next-intl";
import Link from "next/link";
// One row per WAF source: can it be judged, how whole is its latest bucket, how
// close is it to the endpoint ceiling, and is admission overflowing.
//
// Sources that need attention are listed first. Every figure that can be
// unmeasured says so in words: a source with no collected bucket has no
// coverage and no overflow figure, which is not "100%" and not "0".

import { TableScroll } from "@/components/ui/TableScroll";
import type { WafSourceBehavior } from "@/lib/api";
import { NOT_MEASURED, formatCount } from "@/lib/behavior";
import { completenessCode, versionLabel } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { UtcTime } from "@/components/ui/UtcTime";

import { formatPercent } from "@/lib/format";
import {
  FAILURE_STAGES,
  atCeiling,
  collectionTone,
  isOverflowing,
  sortByAttention,
  validationView,
} from "./wafSourceHealth";

import "@/components/ui/controls.css";

function Unmeasured({ meaning }: { meaning: string }) {
  return (
    <span className="muted">
      {NOT_MEASURED}
      <span className="sr-only"> {meaning}</span>
    </span>
  );
}

/** A count line. An unmeasured count is a bare dash: no unit or label follows a missing value. */
function CountLine({
  value,
  label,
  locale,
}: {
  value: number | null;
  label: React.ReactNode;
  locale: "en" | "tr";
}) {
  const t = useTranslations("waf.sources");
  if (value == null) {
    return (
      <span className="cell-sub muted">
        {NOT_MEASURED}
        <span className="sr-only"> {t("notMeasured")}</span>
      </span>
    );
  }
  return (
    <span className="cell-sub">
      {formatCount(value, locale)} {label}
    </span>
  );
}

/**
 * One state per source, from what its collection recorded. The detail (stage, class, counts,
 * last run) sits behind a disclosure; the pill never says "healthy" for absent evidence.
 */
function CollectionCell({ s }: { s: WafSourceBehavior }) {
  const t = useTranslations("waf.sources");
  const c = s.collection;
  const stage = (FAILURE_STAGES as readonly string[]).includes(c.failure_stage ?? "")
    ? c.failure_stage
    : null;
  // The validation and profile columns already say why for the other codes.
  const why =
    c.state !== "not_eligible"
      ? null
      : c.ineligible_code === "collection_disabled"
        ? t("collectionWhy.collection_disabled")
        : c.ineligible_code === "monitoring_disabled"
          ? t("collectionWhy.monitoring_disabled")
          : c.ineligible_code === "source_missing"
            ? t("collectionWhy.source_missing")
            : null;
  let sub: React.ReactNode = null;
  if (c.state === "failed" && c.last_failure_at) {
    sub = stage
      ? t.rich("collectionSub.failedAt", { stage, time: () => <UtcTime value={c.last_failure_at} variant="compact" /> })
      : t.rich("collectionSub.failed", { time: () => <UtcTime value={c.last_failure_at} variant="compact" /> });
  } else if (
    (c.state === "source_busy" || c.state === "capacity_unavailable") &&
    c.skipped_at
  ) {
    sub = t.rich("collectionSub.skipped", { time: () => <UtcTime value={c.skipped_at} variant="compact" /> });
  } else if (c.state === "collecting" && c.last_success_at) {
    sub = t.rich("collectionSub.succeeded", { time: () => <UtcTime value={c.last_success_at} variant="compact" /> });
  }
  const hasDetail =
    c.state !== "not_eligible" &&
    c.state !== "no_run_recorded" &&
    (c.last_run_at != null ||
      c.intervals_due != null ||
      (c.consecutive_failures ?? 0) > 0 ||
      c.failure_class != null);
  return (
    <>
      <span className={`pill pill-quiet ${collectionTone(c.state)}`}>
        {t(`collectionState.${c.state}`)}
      </span>
      {why && <span className="cell-sub">{why}</span>}
      {sub && <span className="cell-sub">{sub}</span>}
      {hasDetail && (
        <details className="cell-sub">
          <summary>{t("collectionDetails.summary")}</summary>
          <ul className="waf-collection-detail">
            {c.last_run_at && (
              <li>{t.rich("collectionDetails.lastRun", { time: () => <UtcTime value={c.last_run_at} variant="compact" /> })}</li>
            )}
            {c.intervals_due != null && c.intervals_collected != null && (
              <li>
                {t("collectionDetails.intervals", {
                  collected: c.intervals_collected,
                  due: c.intervals_due,
                })}
              </li>
            )}
            {(c.consecutive_failures ?? 0) > 0 && (
              <li>{t("collectionDetails.failures", { count: c.consecutive_failures ?? 0 })}</li>
            )}
            {c.failure_class && (
              <li>{t("collectionDetails.errorClass", { name: c.failure_class })}</li>
            )}
          </ul>
        </details>
      )}
    </>
  );
}

function CoverageCell({ s }: { s: WafSourceBehavior }) {
  const t = useTranslations("waf.sources");
  const i18n = useBehaviorI18n();
  const b = s.latest_bucket;
  if (!b) {
    return (
      <Unmeasured meaning={t("noBucketMeaning")} />
    );
  }
  return (
    <>
      {formatPercent(b.endpoint_coverage_ratio, b.coverage_measurement, 1, i18n.locale)}
      <span className="cell-sub">
        <UtcTime value={b.bucket_start} variant="compact" />
      </span>
      {b.completeness !== "COMPLETE" && (
        <span className="cell-sub">
          {t("bucketCompleteness", { completeness: completenessCode(i18n, b.completeness) })}
        </span>
      )}
      {b.in_maintenance && <span className="cell-sub">{t("inMaintenance")}</span>}
    </>
  );
}

function OverflowCell({ s }: { s: WafSourceBehavior }) {
  const t = useTranslations("waf.sources");
  const { locale } = useBehaviorI18n();
  const a = s.admission;
  if (a.buckets_in_range === 0) {
    return (
      <Unmeasured meaning={t("noBucketInRangeMeaning")} />
    );
  }
  const truncated = a.buckets_admission_truncated;
  return (
    <>
      {isOverflowing(s) ? (
        <span className="pill pill-quiet warn">{t("overflowing")}</span>
      ) : (
        <span>{t("none")}</span>
      )}
      <span className="cell-sub">
        {truncated == null ? (
          <>
            {NOT_MEASURED}
            <span className="sr-only"> {t("truncatedNotMeasured")}</span>
          </>
        ) : (
          <>
            {t("truncatedBuckets", {
              truncated: formatCount(truncated, locale),
              total: formatCount(a.buckets_in_range, locale),
            })}
          </>
        )}
      </span>
      <CountLine
        value={a.rejected_over_quota}
        locale={locale}
        label={t("refusedOverQuota")}
      />
      <CountLine
        value={a.below_admission_threshold}
        locale={locale}
        label={t("belowThreshold")}
      />
      <CountLine value={a.untracked_requests} locale={locale} label={t("untracked")} />
    </>
  );
}

export function WafSourcesTable({ sources }: { sources: WafSourceBehavior[] }) {
  const t = useTranslations("waf.sources");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  const rows = sortByAttention(sources);
  // Said once above the table when it holds for every row, not once per row.
  const noBuckets = rows.every((s) => !s.latest_bucket && s.admission.buckets_in_range === 0);
  return (
    <>
    {noBuckets && <p className="waf-table-state">{t("noBucketYet")}</p>}
    <TableScroll label={t("tableLabel")}>
      <table className="table-dense">
        <caption className="sr-only">{t("caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("source")}</th>
            <th scope="col">{t("profile")}</th>
            <th scope="col">{t("validation")}</th>
            <th scope="col">{t("collection")}</th>
            <th scope="col" className="num">
              {t("coverage")}
            </th>
            <th scope="col" className="num">
              {t("activeEndpoints")}
            </th>
            <th scope="col">{t("overflow")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => {
            const v = validationView(s, i18n);
            return (
              <tr key={s.log_source_id}>
                <td className="cell-name">
                  <Link href={`/behavior/waf/sources/${s.log_source_id}`}>{s.log_source_name}</Link>
                  {s.product_label && <span className="cell-sub">{s.product_label}</span>}
                </td>
                <td>
                  {s.enabled ? (
                    t("enabled")
                  ) : (
                    <span className="pill pill-quiet warn">{t("disabled")}</span>
                  )}
                  <span className="cell-sub">{versionLabel(formatCount(s.profile_version, locale))}</span>
                  <span className="cell-sub">
                    <Link href={`/admin/waf-sources/${s.log_source_id}`}>{t("profileSettings")}</Link>
                  </span>
                </td>
                <td>
                  <span className={`pill pill-quiet ${v.tone}`}>{v.label}</span>
                  {v.detail && <span className="cell-sub">{v.detail}</span>}
                </td>
                <td>
                  <CollectionCell s={s} />
                </td>
                <td className="num">
                  <CoverageCell s={s} />
                </td>
                <td className="num">
                  <span title={t("trackedRetired", { count: formatCount(s.total_endpoints, locale) })}>
                    {t("endpointsOfCeiling", {
                      active: formatCount(s.active_endpoints, locale),
                      ceiling: formatCount(s.active_endpoint_ceiling, locale),
                    })}
                  </span>
                  {atCeiling(s) && <span className="cell-sub">{t("atCeiling")}</span>}
                </td>
                <td>
                  <OverflowCell s={s} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
    </>
  );
}
