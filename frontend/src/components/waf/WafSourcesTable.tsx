// One row per WAF source: can it be judged, how whole is its latest bucket, how
// close is it to the endpoint ceiling, and is admission overflowing.
//
// Sources that need attention are listed first. Every figure that can be
// unmeasured says so in words: a source with no collected bucket has no
// coverage and no overflow figure, which is not "100%" and not "0".

import { TableScroll } from "@/components/ui/TableScroll";
import type { WafSourceBehavior } from "@/lib/api";
import { NOT_MEASURED, formatCount } from "@/lib/behavior";
import { formatDateTime, formatDateTimeCompact } from "@/lib/health";

import { formatPercent } from "./wafFormat";
import { atCeiling, isOverflowing, sortByAttention, validationView } from "./wafSourceHealth";

import "@/components/ui/controls.css";

const KEY_BUCKETS_MEANING =
  "Counted per bucket: an endpoint key refused in several buckets of the range counts once for each of them.";

function Unmeasured({ meaning }: { meaning: string }) {
  return (
    <span className="muted">
      {NOT_MEASURED}
      <span className="sr-only"> {meaning}</span>
    </span>
  );
}

function CoverageCell({ s }: { s: WafSourceBehavior }) {
  const b = s.latest_bucket;
  if (!b) {
    return (
      <>
        <Unmeasured meaning="no bucket has been collected" />
        <span className="cell-sub">No bucket collected yet</span>
      </>
    );
  }
  return (
    <>
      {formatPercent(b.endpoint_coverage_ratio, b.coverage_measurement)}
      <span className="cell-sub">
        <span title={formatDateTime(b.bucket_start)}>{formatDateTimeCompact(b.bucket_start)}</span>
      </span>
      {b.completeness !== "COMPLETE" && <span className="cell-sub">{b.completeness} bucket</span>}
      {b.in_maintenance && <span className="cell-sub">In maintenance</span>}
    </>
  );
}

function OverflowCell({ s }: { s: WafSourceBehavior }) {
  const a = s.admission;
  if (a.buckets_in_range === 0) {
    return (
      <>
        <Unmeasured meaning="no bucket was collected in this range" />
        <span className="cell-sub">No bucket collected in range</span>
      </>
    );
  }
  const truncated = a.buckets_admission_truncated;
  return (
    <>
      {isOverflowing(s) ? (
        <span className="pill pill-quiet warn">Overflowing</span>
      ) : (
        <span>None</span>
      )}
      <span className="cell-sub">
        {truncated == null ? NOT_MEASURED : formatCount(truncated)} of {formatCount(a.buckets_in_range)}{" "}
        buckets truncated
      </span>
      <span className="cell-sub">
        {formatCount(a.rejected_over_quota)} refused over quota
        <span className="muted key-buckets" title={KEY_BUCKETS_MEANING}>
          {" "}
          (key-buckets)
        </span>
      </span>
      <span className="cell-sub">
        {formatCount(a.below_admission_threshold)} below admission threshold
        <span className="muted key-buckets" title={KEY_BUCKETS_MEANING}>
          {" "}
          (key-buckets)
        </span>
      </span>
      <span className="cell-sub">{formatCount(a.untracked_requests)} untracked requests</span>
    </>
  );
}

export function WafSourcesTable({ sources }: { sources: WafSourceBehavior[] }) {
  const rows = sortByAttention(sources);
  return (
    <>
    <TableScroll label="WAF sources">
      <table className="table-dense">
        <caption className="sr-only">
          WAF sources with profile and validation status for the current profile version, endpoint
          coverage of the latest collected bucket, active endpoints against the ceiling, and
          admission overflow in the selected range. Sources that need attention are listed first.
          Times are UTC.
        </caption>
        <thead>
          <tr>
            <th scope="col">Source</th>
            <th scope="col">Profile</th>
            <th scope="col">Validation</th>
            <th scope="col" className="num">
              Endpoint coverage
              <span className="cell-sub">latest bucket, % of events</span>
            </th>
            <th scope="col" className="num">
              Active endpoints
              <span className="cell-sub">of ceiling</span>
            </th>
            <th scope="col">
              Admission overflow
              <span className="cell-sub">in range, endpoint keys</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => {
            const v = validationView(s);
            return (
              <tr key={s.log_source_id}>
                <td className="cell-name">
                  <a href={`/behavior/waf/sources/${s.log_source_id}`}>{s.log_source_name}</a>
                  {s.product_label && <span className="cell-sub">{s.product_label}</span>}
                </td>
                <td>
                  {s.enabled ? "Enabled" : <span className="pill pill-quiet warn">Disabled</span>}
                  <span className="cell-sub">v{s.profile_version}</span>
                  <span className="cell-sub">
                    <a href={`/admin/waf-sources/${s.log_source_id}`}>Profile settings</a>
                  </span>
                </td>
                <td>
                  <span className={`pill pill-quiet ${v.tone}`}>{v.label}</span>
                  <span className="cell-sub">{v.detail}</span>
                  {s.enabled && !s.validation_current && (
                    <span className="cell-sub">Not collected</span>
                  )}
                </td>
                <td className="num">
                  <CoverageCell s={s} />
                </td>
                <td className="num">
                  {formatCount(s.active_endpoints)} / {formatCount(s.active_endpoint_ceiling)}
                  <span className="cell-sub">{formatCount(s.total_endpoints)} tracked, retired included</span>
                  {atCeiling(s) && <span className="cell-sub">At the ceiling</span>}
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
    <p className="subtitle">
      A key-bucket is one endpoint key in one collected bucket, so a key refused in several
      buckets counts once for each. Refused over quota means the key met the volume threshold but
      the per-bucket quota for new endpoints, or the source&apos;s active ceiling, was full. Below
      admission threshold is ordinary low-volume noise, not overflow.
    </p>
    </>
  );
}
