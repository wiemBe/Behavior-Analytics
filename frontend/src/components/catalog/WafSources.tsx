"use client";

// WAF sources (UR.5.1): are the WAF sources collecting? Per source: its profile verdict for
// the current profile version, the latest bucket's endpoint coverage and the active endpoints
// against the ceiling. A source with no collected bucket has no coverage (an em dash).

import Link from "next/link";

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { WidgetFrame, WidgetQualifiers } from "@/components/home/WidgetFrame";
import { Unmeasured } from "@/components/home/WidgetParts";
import { TableScroll } from "@/components/ui/TableScroll";
import { truncated } from "@/lib/home/qualifiers";
import { formatPercent } from "@/components/waf/wafFormat";
import { atCeiling, sortByAttention, validationView } from "@/components/waf/wafSourceHealth";

import "./catalog.css";

export const WAF_SOURCE_ROWS = 6;

export function WafSources() {
  const read = homeReads.wafSources();
  const state = useApiResource(read.key, read.fetch);

  return (
    <WidgetFrame
      id="waf-sources"
      title="WAF sources"
      windowText="latest bucket"
      link={{ href: "/behavior/waf", label: "WAF" }}
      state={state}
      what="the WAF sources"
      permission="read:anomalies"
    >
      {({ data }) => {
        if (data.items.length === 0) {
          return (
            <p className="tw-state">
              No WAF source is registered. A source appears here once its WAF profile exists.
            </p>
          );
        }
        const sorted = sortByAttention(data.items);
        const rows = sorted.slice(0, WAF_SOURCE_ROWS);
        return (
          <>
            <TableScroll label="WAF sources">
              <table className="tw-table cw-table">
                <colgroup>
                  <col />
                  <col className="tw-col-badge" />
                  <col className="tw-col-ratio" />
                  <col className="tw-col-eps" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">Source</th>
                    <th scope="col">Profile</th>
                    <th scope="col" className="num">
                      Coverage
                    </th>
                    <th scope="col" className="num">
                      Endpoints
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => {
                    const v = validationView(s);
                    const b = s.latest_bucket;
                    return (
                      <tr key={s.log_source_id}>
                        <td className="tw-name">
                          <Link
                            href={`/behavior/waf/sources/${s.log_source_id}`}
                            title={s.log_source_name}
                          >
                            {s.log_source_name}
                          </Link>
                          {!s.enabled ? <span className="tw-sub">Disabled</span> : null}
                        </td>
                        <td title={v.detail}>
                          <span className={`tw-state-word ${v.tone}`}>{v.label}</span>
                        </td>
                        <td className="num">
                          {b === null ? (
                            <Unmeasured reason="No bucket has been collected" />
                          ) : (
                            formatPercent(b.endpoint_coverage_ratio, b.coverage_measurement)
                          )}
                        </td>
                        <td className="num cw-nowrap">
                          <span className={atCeiling(s) ? "tw-state-word warn" : undefined}>
                            {s.active_endpoints.toLocaleString("en-US")}
                          </span>
                          <span className="tw-sub">
                            of {s.active_endpoint_ceiling.toLocaleString("en-US")}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableScroll>
            <WidgetQualifiers
              qualifiers={data.total > data.items.length ? [truncated(data.items.length, data.total)] : []}
            />
            {sorted.length > rows.length ? (
              <p className="tw-note">
                Showing the {rows.length} that need attention first, of {sorted.length}.
              </p>
            ) : null}
          </>
        );
      }}
    </WidgetFrame>
  );
}
