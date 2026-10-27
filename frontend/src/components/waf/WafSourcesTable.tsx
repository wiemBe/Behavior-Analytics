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
import { atCeiling, isOverflowing, sortByAttention, validationView } from "./wafSourceHealth";

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

function KeyBuckets() {
  const t = useTranslations("waf.sources");
  return (
    <span className="muted key-buckets" title={t("keyBucketsMeaning")}>
      {" "}
      {t("keyBuckets")}
    </span>
  );
}

function CoverageCell({ s }: { s: WafSourceBehavior }) {
  const t = useTranslations("waf.sources");
  const i18n = useBehaviorI18n();
  const b = s.latest_bucket;
  if (!b) {
    return (
      <>
        <Unmeasured meaning={t("noBucketMeaning")} />
        <span className="cell-sub">{t("noBucketYet")}</span>
      </>
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
      <>
        <Unmeasured meaning={t("noBucketInRangeMeaning")} />
        <span className="cell-sub">{t("noBucketInRange")}</span>
      </>
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
        label={
          <>
            {t("refusedOverQuota")}
            <KeyBuckets />
          </>
        }
      />
      <CountLine
        value={a.below_admission_threshold}
        locale={locale}
        label={
          <>
            {t("belowThreshold")}
            <KeyBuckets />
          </>
        }
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
  return (
    <>
    <TableScroll label={t("tableLabel")}>
      <table className="table-dense">
        <caption className="sr-only">{t("caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("source")}</th>
            <th scope="col">{t("profile")}</th>
            <th scope="col">{t("validation")}</th>
            <th scope="col" className="num">
              {t("coverage")}
              <span className="cell-sub">{t("coverageSub")}</span>
            </th>
            <th scope="col" className="num">
              {t("activeEndpoints")}
              <span className="cell-sub">{t("ofCeiling")}</span>
            </th>
            <th scope="col">
              {t("overflow")}
              <span className="cell-sub">{t("overflowSub")}</span>
            </th>
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
                  <span className="cell-sub">{v.detail}</span>
                  {s.enabled && !s.validation_current && (
                    <span className="cell-sub">{t("notCollected")}</span>
                  )}
                </td>
                <td className="num">
                  <CoverageCell s={s} />
                </td>
                <td className="num">
                  {t("endpointsOfCeiling", {
                    active: formatCount(s.active_endpoints, locale),
                    ceiling: formatCount(s.active_endpoint_ceiling, locale),
                  })}
                  <span className="cell-sub">
                    {t("trackedRetired", { count: formatCount(s.total_endpoints, locale) })}
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
    <p className="subtitle">{t("note")}</p>
    </>
  );
}
