"use client";

// Recent anomalies (UR.4.3): the latest active anomalies and how many are active.
//
// The badge is the response's `total`, worded "n active". An empty list is not
// "all clear": it says how many sources are not being judged. The unified list
// includes WAF anomalies, which are named by their endpoint.

import Link from "next/link";

import { TableScroll } from "@/components/ui/TableScroll";
import { stateLabel } from "@/components/waf/wafLabels";
import { useApiResource } from "@/hooks/useApiResource";
import { homeReads, sevTone } from "@/lib/api";
import { formatRatio, stateTone } from "@/lib/behavior";
import { formatDateTime, formatDateTimeCompact } from "@/lib/health";
import { RECENT_ANOMALIES_LIMIT, recentAnomalies } from "@/lib/home/recentAnomalies";

import { Gate, secondary, Unmeasured, When, Widget, WidgetLoading } from "./WidgetParts";

export function RecentAnomalies() {
  const listRead = homeReads.activeAnomalies(RECENT_ANOMALIES_LIMIT);
  const summaryRead = homeReads.anomalySummary();
  const list = useApiResource(listRead.key, listRead.fetch);
  const summary = useApiResource(summaryRead.key, summaryRead.fetch);
  const ok = list.status === "ok" ? recentAnomalies(list.data, secondary(summary).data) : null;

  return (
    <Widget
      id="recent-anomalies"
      title="Recent anomalies"
      windowText="active now"
      badge={ok ? ok.badge : undefined}
      viewAll={{ href: "/anomalies", label: "View all" }}
    >
      <Gate state={list} what="the active anomalies" permission="read:anomalies">
        {({ data }) => {
          const sum = secondary(summary);
          // The empty state quotes the not-judged count, so wait for it.
          if (data.items.length === 0 && sum.pending) {
            return <WidgetLoading what="the not-judged source count" />;
          }
          const view = recentAnomalies(data, sum.data);
          if (view.empty !== null) return <p className="tw-state">{view.empty}</p>;
          return (
            <>
              <TableScroll label="Recent anomalies">
                <table className="tw-table">
                  <colgroup>
                    <col className="tw-col-time tw-unfold" />
                    <col />
                    <col className="tw-col-badge tw-unfold" />
                    <col className="tw-col-ratio" />
                    <col className="tw-col-action" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col" className="tw-unfold">
                        Detected
                      </th>
                      <th scope="col" className="tw-lead-col">
                        Anomaly
                      </th>
                      <th scope="col" className="tw-unfold">
                        Severity, state
                      </th>
                      <th scope="col" className="num">
                        Deviation
                        <span className="tw-sub tw-wrap">vs expected</span>
                      </th>
                      <th scope="col">
                        <span className="sr-only">Investigate</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.rows.map((r) => (
                      <tr key={r.id}>
                        <td className="tw-unfold">
                          <When iso={r.detectedAt} />
                        </td>
                        <td className="tw-name tw-lead-col">
                          <Link href={r.sourceHref} title={r.subject}>
                            {r.subject}
                          </Link>
                          <span className="tw-sub" title={r.sourceName ?? undefined}>
                            {r.typeLabel}
                            {r.sourceName !== null && ` · ${r.sourceName}`}
                          </span>
                          {/* Below the widget's breakpoint the Detected and Severity columns
                              fold into this line; above it the line is hidden. */}
                          <span className="tw-sub tw-wrap tw-fold-line">
                            <span className={`tw-state-word ${sevTone(r.severity)}`}>
                              {r.severity}
                            </span>
                            {" · "}
                            <span className={`tw-state-word ${stateTone(r.state)}`}>
                              {stateLabel(r.state)}
                            </span>
                            {" · "}
                            <time dateTime={r.detectedAt} title={formatDateTime(r.detectedAt)}>
                              {formatDateTimeCompact(r.detectedAt)}
                            </time>
                          </span>
                        </td>
                        <td className="tw-unfold">
                          <span className={`pill ${sevTone(r.severity)}`}>{r.severity}</span>
                          <span className={`tw-sub tw-wrap tw-state-word ${stateTone(r.state)}`}>
                            {stateLabel(r.state)}
                          </span>
                        </td>
                        <td className="num">
                          {r.deviation === null ? (
                            <Unmeasured reason="No deviation ratio" />
                          ) : (
                            formatRatio(r.deviation)
                          )}
                        </td>
                        <td>
                          <Link
                            className="tw-link"
                            href={r.href}
                            aria-label={`Investigate ${r.subject}`}
                          >
                            Investigate
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
              {view.shown && <p className="tw-note">{view.shown}.</p>}
            </>
          );
        }}
      </Gate>
    </Widget>
  );
}
